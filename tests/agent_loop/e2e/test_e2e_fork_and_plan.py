from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop, e2e_config
from tests.backend.data.mistral import mistral_completion
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    AskUserQuestionResult,
)
from vibe.core.types import PlanReviewEndedEvent, PlanReviewRequestedEvent, Role
from vibe.core.utils.tags import VIBE_WARNING_TAG

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

    forked = await agent.fork()

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
    forked = await agent.fork(first_user_id)

    contents = [m.content for m in forked.messages if m.role != Role.system]
    assert contents == ["Turn one", "First"]


@pytest.mark.asyncio
async def test_fork_from_unknown_message_id_raises(mistral_api: MistralAPI) -> None:
    # An unknown anchor id is rejected rather than silently forking everything.
    mistral_api.reply(mistral_completion("Hi"))
    agent = build_e2e_agent_loop()
    _ = [event async for event in agent.act("Hello")]

    with pytest.raises(ValueError, match="unknown message_id"):
        await agent.fork("does-not-exist")


@pytest.mark.asyncio
async def test_fork_from_assistant_message_id_raises(mistral_api: MistralAPI) -> None:
    # Forking is only allowed from user turns; an assistant anchor is rejected.
    mistral_api.reply(mistral_completion("Hi"))
    agent = build_e2e_agent_loop()
    _ = [event async for event in agent.act("Hello")]

    assistant_id = _assistant_message_id(agent)
    with pytest.raises(ValueError, match="only supported for user messages"):
        await agent.fork(assistant_id)


def _plan_agent(mistral_api: MistralAPI) -> Any:
    mistral_api.reply(
        mistral_completion("", tool_calls=EXIT_PLAN_TOOL_CALL),
        mistral_completion("Staying in plan mode."),
    )
    return build_e2e_agent_loop(
        config=e2e_config(enabled_tools=["exit_plan_mode"]),
        agent_name=BuiltinAgentName.PLAN,
    )


@pytest.mark.asyncio
async def test_plan_mode_emits_review_requested_and_ended_events(
    mistral_api: MistralAPI,
) -> None:
    # An exit_plan_mode round trip brackets the tool with review-requested/ended events.
    agent = _plan_agent(mistral_api)

    async def stay_in_plan(_: AskUserQuestionArgs) -> AskUserQuestionResult:
        return AskUserQuestionResult(cancelled=True, answers=[])

    agent.set_user_input_callback(stay_in_plan)

    events = [event async for event in agent.act("Make a plan")]

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

    agent.set_user_input_callback(edit_plan_then_decline)

    async for event in agent.act("Make a plan"):
        if isinstance(event, PlanReviewRequestedEvent):
            plan_path = event.file_path

    injected = [m for m in agent.messages if getattr(m, "injected", False)]
    assert any(
        m.content and VIBE_WARNING_TAG in m.content and "# Updated plan" in m.content
        for m in injected
    )
