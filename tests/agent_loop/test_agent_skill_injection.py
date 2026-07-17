from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.skills.conftest import create_skill
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.types import (
    AssistantEvent,
    BaseEvent,
    Role,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)


async def _act_and_collect(agent_loop: AgentLoop, prompt: str) -> list[BaseEvent]:
    return [ev async for ev in agent_loop.act(prompt)]


def _make_loop(
    tmp_path: Path, body: str = "Do the skill thing.", turns: int = 1
) -> AgentLoop:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    create_skill(skills_dir, "test-skill", "A test skill", body=body)
    config = build_test_vibe_config(skill_paths=[skills_dir], enabled_tools=["skill"])
    return build_test_agent_loop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=FakeBackend([
            [mock_llm_chunk(content="Done using the skill.")] for _ in range(turns)
        ]),
    )


@pytest.mark.asyncio
async def test_invoking_skill_injects_tool_call_and_result(tmp_path: Path) -> None:
    agent_loop = _make_loop(tmp_path)

    events = await _act_and_collect(agent_loop, "/test-skill")

    assert [type(e) for e in events] == [
        UserMessageEvent,
        ToolCallEvent,
        ToolResultEvent,
        AssistantEvent,
    ]
    call_event = events[1]
    result_event = events[2]
    assert isinstance(call_event, ToolCallEvent)
    assert isinstance(result_event, ToolResultEvent)
    assert call_event.tool_name == "skill"
    assert result_event.tool_name == "skill"
    assert call_event.tool_call_id == result_event.tool_call_id


@pytest.mark.asyncio
async def test_user_turn_keeps_literal_command(tmp_path: Path) -> None:
    agent_loop = _make_loop(tmp_path)

    await _act_and_collect(agent_loop, "/test-skill fix the bug")

    user_msgs = [m for m in agent_loop.messages if m.role == Role.user]
    assert user_msgs[-1].content == "/test-skill fix the bug"


@pytest.mark.asyncio
async def test_skill_content_lands_in_tool_message(tmp_path: Path) -> None:
    agent_loop = _make_loop(tmp_path, body="Follow the plan carefully.")

    await _act_and_collect(agent_loop, "/test-skill")

    assistant_with_call = next(
        m for m in agent_loop.messages if m.role == Role.assistant and m.tool_calls
    )
    tool_call = (
        assistant_with_call.tool_calls[0] if assistant_with_call.tool_calls else None
    )
    assert tool_call is not None
    assert tool_call.function.name == "skill"

    tool_msg = next(m for m in agent_loop.messages if m.role == Role.tool)
    assert tool_msg.tool_call_id == tool_call.id
    assert tool_msg.name == "skill"
    assert "Follow the plan carefully." in (tool_msg.content or "")
    assert '<skill_content name="test-skill">' in (tool_msg.content or "")


@pytest.mark.asyncio
async def test_skill_content_injected_only_once(tmp_path: Path) -> None:
    agent_loop = _make_loop(tmp_path, body="Follow the plan carefully.", turns=2)

    await _act_and_collect(agent_loop, "/test-skill")
    await _act_and_collect(agent_loop, "/test-skill")

    skill_tool_msgs = [
        m for m in agent_loop.messages if m.role == Role.tool and m.name == "skill"
    ]
    assert len(skill_tool_msgs) == 2
    full_loads = [
        m
        for m in skill_tool_msgs
        if '<skill_content name="test-skill">' in (m.content or "")
    ]
    assert len(full_loads) == 1
    assert "already loaded" in (skill_tool_msgs[1].content or "")


@pytest.mark.asyncio
async def test_reinvocation_still_emits_events(tmp_path: Path) -> None:
    agent_loop = _make_loop(tmp_path, turns=2)

    await _act_and_collect(agent_loop, "/test-skill")
    events = await _act_and_collect(agent_loop, "/test-skill")

    assert [type(e) for e in events] == [
        UserMessageEvent,
        ToolCallEvent,
        ToolResultEvent,
        AssistantEvent,
    ]


@pytest.mark.asyncio
async def test_inject_user_context_emits_skill_events_via_callback(
    tmp_path: Path,
) -> None:
    agent_loop = _make_loop(tmp_path)
    events: list[BaseEvent] = []

    async def capture(event: BaseEvent) -> None:
        events.append(event)

    await agent_loop.inject_user_context(
        "/test-skill", as_message=True, inject_implicit=True, on_event=capture
    )

    assert [type(e) for e in events] == [ToolCallEvent, ToolResultEvent]


@pytest.mark.asyncio
async def test_plain_prompt_does_not_inject_skill(tmp_path: Path) -> None:
    agent_loop = _make_loop(tmp_path)

    events = await _act_and_collect(agent_loop, "just a normal question")

    assert not any(isinstance(e, ToolCallEvent) for e in events)
    assert not any(m.role == Role.tool for m in agent_loop.messages)


@pytest.mark.asyncio
async def test_inject_user_context_injects_skill_and_forwards_events(
    tmp_path: Path,
) -> None:
    agent_loop = _make_loop(tmp_path, body="Queued skill body.")
    events: list[BaseEvent] = []

    await agent_loop.inject_user_context(
        "/test-skill",
        as_message=True,
        inject_implicit=True,
        on_event=lambda e: _record(events, e),
    )

    assert [type(e) for e in events] == [ToolCallEvent, ToolResultEvent]
    tool_msg = next(m for m in agent_loop.messages if m.role == Role.tool)
    assert tool_msg.name == "skill"
    assert "Queued skill body." in (tool_msg.content or "")


@pytest.mark.asyncio
async def test_inject_user_context_without_flag_does_not_inject_skill(
    tmp_path: Path,
) -> None:
    agent_loop = _make_loop(tmp_path)

    await agent_loop.inject_user_context("/test-skill", as_message=True)

    assert not any(m.role == Role.tool for m in agent_loop.messages)


async def _record(sink: list[BaseEvent], event: BaseEvent) -> None:
    sink.append(event)
