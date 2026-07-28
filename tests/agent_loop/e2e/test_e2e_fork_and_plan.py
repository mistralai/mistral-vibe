from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop
from tests.backend.data.mistral import mistral_completion
from tests.conftest import build_test_vibe_config
from vibe.app_server._runtime import AgentRuntimeFactory
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    AskUserQuestionResult,
)
from vibe.core.types import (
    BaseEvent,
    PlanReviewEndedEvent,
    PlanReviewRequestedEvent,
    Role,
    UserInputRequestEvent,
)
from vibe.core.utils.tags import VIBE_WARNING_TAG
from vibe.questions import UserAnswer as Answer

EXIT_PLAN_TOOL_CALL = [
    {
        "id": "call_exit_plan_1",
        "function": {"name": "exit_plan_mode", "arguments": "{}"},
        "index": 0,
    }
]


def _user_message_ids(agent: Any) -> list[str]:
    return [m.message_id for m in agent.messages if m.role == Role.user]


def _assistant_message_id(agent: Any) -> str:
    return next(m.message_id for m in agent.messages if m.role == Role.assistant)


@pytest.mark.asyncio
async def test_fork_copies_all_non_system_messages(mistral_api: MistralAPI) -> None:
    # fork() with no anchor clones every non-system message into the child loop.
    mistral_api.reply(mistral_completion("Hi there!"))
    agent = build_e2e_agent_loop()
    _ = [event async for event in agent.act("Hello")]

    forked = await AgentRuntimeFactory().fork(agent, None)

    non_system = [m for m in forked.messages if m.role != Role.system]
    assert [m.role for m in non_system] == [Role.user, Role.assistant]
    assert forked.parent_session_id == agent.session_id
    assert forked.session_id != agent.session_id


@pytest.mark.asyncio
async def test_fork_from_message_id_truncates_at_next_user_turn(
    mistral_api: MistralAPI,
) -> None:
    # Forking from a user message keeps that turn but drops everything from the next one.
    mistral_api.reply(mistral_completion("First"), mistral_completion("Second"))
    agent = build_e2e_agent_loop()
    _ = [event async for event in agent.act("Turn one")]
    _ = [event async for event in agent.act("Turn two")]

    first_user_id = _user_message_ids(agent)[0]
    forked = await AgentRuntimeFactory().fork(agent, first_user_id)

    contents = [m.content for m in forked.messages if m.role != Role.system]
    assert contents == ["Turn one", "First"]


@pytest.mark.asyncio
async def test_fork_from_unknown_message_id_raises(mistral_api: MistralAPI) -> None:
    # An unknown anchor id is rejected rather than silently forking everything.
    mistral_api.reply(mistral_completion("Hi"))
    agent = build_e2e_agent_loop()
    _ = [event async for event in agent.act("Hello")]

    with pytest.raises(ValueError, match="unknown message_id"):
        await AgentRuntimeFactory().fork(agent, "does-not-exist")


@pytest.mark.asyncio
async def test_fork_from_assistant_message_id_raises(mistral_api: MistralAPI) -> None:
    # Forking is only allowed from user turns; an assistant anchor is rejected.
    mistral_api.reply(mistral_completion("Hi"))
    agent = build_e2e_agent_loop()
    _ = [event async for event in agent.act("Hello")]

    assistant_id = _assistant_message_id(agent)
    with pytest.raises(ValueError, match="only supported for user messages"):
        await AgentRuntimeFactory().fork(agent, assistant_id)


def _plan_agent(mistral_api: MistralAPI) -> Any:
    mistral_api.reply(
        mistral_completion("", tool_calls=EXIT_PLAN_TOOL_CALL),
        mistral_completion("Staying in plan mode."),
    )
    return build_e2e_agent_loop(
        config=build_test_vibe_config(enabled_tools=["exit_plan_mode"]),
        agent_name=BuiltinAgentName.PLAN,
    )


async def _act_with_user_input(
    agent: Any,
    handler: Callable[[AskUserQuestionArgs], Awaitable[AskUserQuestionResult]],
) -> list[BaseEvent]:
    events: list[BaseEvent] = []
    async for event in agent.act("Make a plan"):
        events.append(event)
        if isinstance(event, UserInputRequestEvent):
            result = await handler(cast(AskUserQuestionArgs, event.args))
            agent.resolve_user_input_request(event.request_id, result)
    return events


@pytest.mark.asyncio
async def test_plan_mode_emits_review_requested_and_ended_events(
    mistral_api: MistralAPI,
) -> None:
    # An exit_plan_mode round trip brackets the tool with review-requested/ended events.
    agent = _plan_agent(mistral_api)

    async def stay_in_plan(_: AskUserQuestionArgs) -> AskUserQuestionResult:
        return AskUserQuestionResult(cancelled=True, answers=[])

    events = await _act_with_user_input(agent, stay_in_plan)

    assert any(isinstance(e, PlanReviewRequestedEvent) for e in events)
    assert any(isinstance(e, PlanReviewEndedEvent) for e in events)


@pytest.mark.asyncio
async def test_plan_mode_injects_updated_plan_when_file_changed(
    mistral_api: MistralAPI,
) -> None:
    # If the plan file changes during review, its new content is injected back as context.
    agent = _plan_agent(mistral_api)

    plan_path: Path | None = None

    async def edit_plan_then_decline(_: AskUserQuestionArgs) -> AskUserQuestionResult:
        assert plan_path is not None
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text("# Updated plan\nStep 1")
        return AskUserQuestionResult(cancelled=True, answers=[])

    async for event in agent.act("Make a plan"):
        if isinstance(event, PlanReviewRequestedEvent):
            plan_path = event.file_path
        elif isinstance(event, UserInputRequestEvent):
            result = await edit_plan_then_decline(cast(AskUserQuestionArgs, event.args))
            agent.resolve_user_input_request(event.request_id, result)

    injected = [m for m in agent.messages if getattr(m, "injected", False)]
    assert any(
        m.content and VIBE_WARNING_TAG in m.content and "# Updated plan" in m.content
        for m in injected
    )


@pytest.mark.asyncio
async def test_plan_mode_exit_switches_agent_mid_turn(mistral_api: MistralAPI) -> None:
    # Approving exit_plan_mode runs a real agent switch inside act(). The reload
    # mutates shared AgentLoop state, so it must apply atomically relative to the
    # in-flight turn rather than interleaving with it.
    agent = _plan_agent(mistral_api)

    async def approve(_: AskUserQuestionArgs) -> AskUserQuestionResult:
        return AskUserQuestionResult(
            cancelled=False,
            answers=[
                Answer(
                    question="q",
                    answer="Yes, and request approval for edits",
                    is_other=False,
                )
            ],
        )

    await _act_with_user_input(agent, approve)

    assert agent.agent_profile.name == BuiltinAgentName.DEFAULT
