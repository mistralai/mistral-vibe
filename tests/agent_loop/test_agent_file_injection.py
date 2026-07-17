from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
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


def _make_loop(turns: int = 1) -> AgentLoop:
    config = build_test_vibe_config(enabled_tools=["read_file"])
    return build_test_agent_loop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=FakeBackend([
            [mock_llm_chunk(content="Done reading the file.")] for _ in range(turns)
        ]),
    )


def _read_file_tool_messages(agent_loop: AgentLoop) -> list[str]:
    return [
        m.content or ""
        for m in agent_loop.messages
        if m.role == Role.tool and m.name == "read_file"
    ]


@pytest.mark.asyncio
async def test_mentioning_file_injects_read_file_call_and_result(
    tmp_working_directory: Path,
) -> None:
    (tmp_working_directory / "notes.md").write_text("hello from notes")
    agent_loop = _make_loop()

    events = await _act_and_collect(agent_loop, "look at @notes.md")

    types = [type(e) for e in events]
    assert types[0] is UserMessageEvent
    assert types[-1] is AssistantEvent
    call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(call_events) == 1
    assert len(result_events) == 1
    assert call_events[0].tool_name == "read_file"
    assert result_events[0].tool_name == "read_file"
    assert call_events[0].tool_call_id == result_events[0].tool_call_id


@pytest.mark.asyncio
async def test_user_turn_keeps_literal_mention(tmp_working_directory: Path) -> None:
    (tmp_working_directory / "notes.md").write_text("hello")
    agent_loop = _make_loop()

    await _act_and_collect(agent_loop, "look at @notes.md please")

    user_msgs = [m for m in agent_loop.messages if m.role == Role.user]
    assert user_msgs[-1].content == "look at @notes.md please"


@pytest.mark.asyncio
async def test_file_content_lands_in_tool_message(tmp_working_directory: Path) -> None:
    (tmp_working_directory / "notes.md").write_text("secret marker line")
    agent_loop = _make_loop()

    await _act_and_collect(agent_loop, "read @notes.md")

    assistant_with_call = next(
        m for m in agent_loop.messages if m.role == Role.assistant and m.tool_calls
    )
    tool_call = (
        assistant_with_call.tool_calls[0] if assistant_with_call.tool_calls else None
    )
    assert tool_call is not None
    assert tool_call.function.name == "read_file"

    tool_msg = next(m for m in agent_loop.messages if m.role == Role.tool)
    assert tool_msg.tool_call_id == tool_call.id
    assert tool_msg.name == "read_file"
    assert "secret marker line" in (tool_msg.content or "")


@pytest.mark.asyncio
async def test_file_reinjected_every_turn_without_dedup(
    tmp_working_directory: Path,
) -> None:
    file_path = tmp_working_directory / "notes.md"
    file_path.write_text("first content")
    agent_loop = _make_loop(turns=2)

    await _act_and_collect(agent_loop, "read @notes.md")
    file_path.write_text("second content")
    await _act_and_collect(agent_loop, "read @notes.md")

    tool_messages = _read_file_tool_messages(agent_loop)
    assert len(tool_messages) == 2
    assert "first content" in tool_messages[0]
    assert "second content" in tool_messages[1]


@pytest.mark.asyncio
async def test_multiple_files_inject_multiple_calls(
    tmp_working_directory: Path,
) -> None:
    (tmp_working_directory / "a.txt").write_text("alpha content")
    (tmp_working_directory / "b.txt").write_text("beta content")
    agent_loop = _make_loop()

    await _act_and_collect(agent_loop, "compare @a.txt and @b.txt")

    tool_messages = _read_file_tool_messages(agent_loop)
    assert len(tool_messages) == 2
    joined = "\n".join(tool_messages)
    assert "alpha content" in joined
    assert "beta content" in joined


@pytest.mark.asyncio
async def test_plain_prompt_does_not_inject_file(tmp_working_directory: Path) -> None:
    agent_loop = _make_loop()

    events = await _act_and_collect(agent_loop, "just a normal question")

    assert not any(isinstance(e, ToolCallEvent) for e in events)
    assert not any(m.role == Role.tool for m in agent_loop.messages)


@pytest.mark.asyncio
async def test_inject_user_context_injects_file_via_callback(
    tmp_working_directory: Path,
) -> None:
    (tmp_working_directory / "notes.md").write_text("queued file body")
    agent_loop = _make_loop()
    events: list[BaseEvent] = []

    async def capture(event: BaseEvent) -> None:
        events.append(event)

    await agent_loop.inject_user_context(
        "read @notes.md", as_message=True, inject_implicit=True, on_event=capture
    )

    assert [type(e) for e in events] == [ToolCallEvent, ToolResultEvent]
    tool_msg = next(m for m in agent_loop.messages if m.role == Role.tool)
    assert tool_msg.name == "read_file"
    assert "queued file body" in (tool_msg.content or "")
