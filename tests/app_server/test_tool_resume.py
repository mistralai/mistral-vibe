from __future__ import annotations

import json

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server._projection import project_history
from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    PublicEffectEntry,
)
from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import SessionLoader
from vibe.core.tools.models import ToolPermission
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["read_file", "edit"])
async def test_resumed_tool_uses_typed_terminal_result(
    tool_name: str, tmp_path
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    match tool_name:
        case "read_file":
            arguments = {"file_path": str(target)}
            expected_verb = "Read"
            expected_message = "1 line from sample.txt"
        case "edit":
            arguments = {
                "file_path": str(target),
                "old_string": "before",
                "new_string": "after",
            }
            expected_verb = "Edited"
            expected_message = "sample.txt"
        case _:
            raise AssertionError(tool_name)

    tool_call = ToolCall(
        id=f"{tool_name}-1",
        index=0,
        function=FunctionCall(name=tool_name, arguments=json.dumps(arguments)),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    config = build_test_vibe_config(
        bypass_tool_permissions=True,
        enabled_tools=[tool_name],
        tools={tool_name: {"permission": ToolPermission.ALWAYS.value}},
        session_logging=logging,
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True, cwd=tmp_path
    )
    session = await create_test_app_server_session(agent_loop)
    session_id = session.session_id
    session_dir = agent_loop.session_logger.session_dir
    assert session_dir is not None
    try:
        _ = [event async for event in session.act("Use the tool")]
        live = next(
            entry for entry in session.history if isinstance(entry, PublicEffectEntry)
        )
        assert isinstance(live.state, CompletedEffectState)
        assert live.state.display.verb == expected_verb
        assert live.state.display.message == expected_message
        persisted = next(
            message
            for message in agent_loop.messages
            if message.role is Role.tool and message.tool_call_id == tool_call.id
        )
        assert persisted.tool_result is not None
        assert persisted.tool_result.presentation is not None
        assistant = next(
            message
            for message in agent_loop.messages
            if message.role is Role.assistant and message.tool_calls
        )
        assert assistant.tool_calls is not None
        assert assistant.tool_calls[0].presentation is not None
    finally:
        await session.close()

    messages, _ = SessionLoader.load_session(session_dir)
    resumed_loop = build_test_agent_loop(
        config=config, backend=FakeBackend(), cwd=tmp_path
    )
    resumed_loop.session_id = session_id
    resumed_loop.session_logger.resume_existing_session(session_id, session_dir)
    system_messages = [
        message for message in resumed_loop.messages if message.role is Role.system
    ]
    resumed_loop.messages.reset([*system_messages, *messages])
    resumed_loop.tool_manager._all_tools.clear()
    resumed_loop.tool_manager._tool_variants_by_name.clear()
    try:
        restored = next(
            entry
            for entry in project_history(resumed_loop)
            if isinstance(entry, PublicEffectEntry)
        )
        assert isinstance(restored.state, CompletedEffectState)
        assert restored.state.display.verb == expected_verb
        assert restored.state.display.message == expected_message
        assert restored.state.output == live.state.output
        assert isinstance(restored.state.output, dict)
        if tool_name == "read_file":
            assert restored.state.output["filePath"] == str(target)
            assert isinstance(restored.state.output["content"], str)
        else:
            assert restored.state.output == {
                "file": str(target),
                "oldString": "before",
                "newString": "after",
                "occurrences": [
                    {"startLine": 1, "oldText": "before", "newText": "after"}
                ],
            }
    finally:
        await resumed_loop.aclose()
        await resumed_loop.telemetry_client.aclose()


@pytest.mark.asyncio
async def test_resumed_incomplete_tool_call_is_cancelled(tmp_path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("content\n", encoding="utf-8")
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    config = build_test_vibe_config(
        bypass_tool_permissions=True,
        enabled_tools=["read_file"],
        tools={"read_file": {"permission": ToolPermission.ALWAYS.value}},
        session_logging=logging,
    )
    agent_loop = build_test_agent_loop(config=config, backend=FakeBackend())
    tool_call = ToolCall(
        id="read_file-incomplete",
        index=0,
        function=FunctionCall(
            name="read_file", arguments=json.dumps({"file_path": str(target)})
        ),
    )
    agent_loop.messages.append(LLMMessage(role=Role.assistant, tool_calls=[tool_call]))
    await agent_loop._save_messages()
    session_dir = agent_loop.session_logger.session_dir
    assert session_dir is not None
    await agent_loop.aclose()
    await agent_loop.telemetry_client.aclose()

    messages, _ = SessionLoader.load_session(session_dir)
    resumed_loop = build_test_agent_loop(config=config, backend=FakeBackend())
    system_messages = [
        message for message in resumed_loop.messages if message.role is Role.system
    ]
    resumed_loop.messages.reset([*system_messages, *messages])
    try:
        effect = next(
            entry
            for entry in project_history(resumed_loop)
            if isinstance(entry, PublicEffectEntry)
        )
    finally:
        await resumed_loop.aclose()
        await resumed_loop.telemetry_client.aclose()

    assert effect.generation_status == "completed"
    assert isinstance(effect.state, CancelledEffectState)
    assert effect.state.reason == "Tool did not complete before the session ended"
    assert effect.state.output_text == ""
