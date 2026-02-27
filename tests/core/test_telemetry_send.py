from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_tool import FakeTool, FakeToolArgs
from vibe.core.agent_loop import ToolDecision, ToolExecutionResponse
from vibe.core.config import Backend
from vibe.core.llm.format import ResolvedToolCall
from vibe.core.telemetry.send import DATALAKE_EVENTS_URL, TelemetryClient
from vibe.core.tools.base import BaseTool, ToolPermission
from vibe.core.utils import get_user_agent

_original_send_telemetry_event = TelemetryClient.send_telemetry_event
from vibe.core.tools.builtins.write_file import WriteFile, WriteFileArgs


def _make_resolved_tool_call(
    tool_name: str, args_dict: dict[str, Any]
) -> ResolvedToolCall:
    if tool_name == "write_file":
        validated = WriteFileArgs(
            path="foo.txt", content="x", overwrite=args_dict.get("overwrite", False)
        )
        cls: type[BaseTool] = WriteFile
    else:
        validated = FakeToolArgs()
        cls = FakeTool
    return ResolvedToolCall(
        tool_name=tool_name, tool_class=cls, validated_args=validated, call_id="call_1"
    )


def _run_telemetry_tasks() -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.close()


class TestTelemetryClient:
    def test_send_telemetry_event_does_nothing_when_api_key_is_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        env_key = config.get_provider_for_model(
            config.get_active_model()
        ).api_key_env_var
        monkeypatch.delenv(env_key, raising=False)
        client = TelemetryClient(config_getter=lambda: config)
        assert client._get_mistral_api_key() is None
        client._client = MagicMock()
        client._client.post = AsyncMock()

        client.send_telemetry_event("vibe.test", {})
        _run_telemetry_tasks()

        client._client.post.assert_not_called()

    def test_send_telemetry_event_does_nothing_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            TelemetryClient, "send_telemetry_event", _original_send_telemetry_event
        )
        config = build_test_vibe_config(enable_telemetry=False)
        env_key = config.get_provider_for_model(
            config.get_active_model()
        ).api_key_env_var
        monkeypatch.setenv(env_key, "sk-test")
        client = TelemetryClient(config_getter=lambda: config)
        client._client = MagicMock()
        client._client.post = AsyncMock()

        client.send_telemetry_event("vibe.test", {})
        _run_telemetry_tasks()

        client._client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_telemetry_event_posts_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            TelemetryClient, "send_telemetry_event", _original_send_telemetry_event
        )
        config = build_test_vibe_config(enable_telemetry=True)
        env_key = config.get_provider_for_model(
            config.get_active_model()
        ).api_key_env_var
        monkeypatch.setenv(env_key, "sk-test")
        client = TelemetryClient(config_getter=lambda: config)
        mock_post = AsyncMock(return_value=MagicMock(status_code=204))
        client._client = MagicMock()
        client._client.post = mock_post
        client._client.aclose = AsyncMock()

        client.send_telemetry_event("vibe.test_event", {"key": "value"})
        await client.aclose()

        mock_post.assert_called_once_with(
            DATALAKE_EVENTS_URL,
            json={"event": "vibe.test_event", "properties": {"key": "value"}},
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-test",
                "User-Agent": get_user_agent(Backend.MISTRAL),
            },
        )

    def test_send_tool_call_finished_payload_shape(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)
        tool_call = _make_resolved_tool_call("todo", {})
        decision = ToolDecision(
            verdict=ToolExecutionResponse.EXECUTE, approval_type=ToolPermission.ALWAYS
        )

        client.send_tool_call_finished(
            tool_call=tool_call,
            status="success",
            decision=decision,
            agent_profile_name="default",
        )

        assert len(telemetry_events) == 1
        event_name = telemetry_events[0]["event_name"]
        assert event_name == "vibe.tool_call_finished"
        properties = telemetry_events[0]["properties"]
        assert properties["tool_name"] == "todo"
        assert properties["status"] == "success"
        assert properties["decision"] == "execute"
        assert properties["approval_type"] == "always"
        assert properties["agent_profile_name"] == "default"
        assert properties["nb_files_created"] == 0
        assert properties["nb_files_modified"] == 0

    def test_send_tool_call_finished_nb_files_created_write_file_new(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)
        tool_call = _make_resolved_tool_call("write_file", {"overwrite": False})

        client.send_tool_call_finished(
            tool_call=tool_call,
            status="success",
            decision=None,
            agent_profile_name="default",
            result={"file_existed": False},
        )

        assert telemetry_events[0]["properties"]["nb_files_created"] == 1
        assert telemetry_events[0]["properties"]["nb_files_modified"] == 0

    def test_send_tool_call_finished_nb_files_modified_write_file_overwrite(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)
        tool_call = _make_resolved_tool_call("write_file", {"overwrite": True})

        client.send_tool_call_finished(
            tool_call=tool_call,
            status="success",
            decision=None,
            agent_profile_name="default",
            result={"file_existed": True},
        )

        assert telemetry_events[0]["properties"]["nb_files_created"] == 0
        assert telemetry_events[0]["properties"]["nb_files_modified"] == 1

    def test_send_tool_call_finished_decision_none(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)
        tool_call = _make_resolved_tool_call("todo", {})

        client.send_tool_call_finished(
            tool_call=tool_call,
            status="skipped",
            decision=None,
            agent_profile_name="default",
        )

        assert telemetry_events[0]["properties"]["decision"] is None
        assert telemetry_events[0]["properties"]["approval_type"] is None

    def test_send_user_copied_text_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_user_copied_text("hello world")

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.user_copied_text"
        assert telemetry_events[0]["properties"]["text_length"] == 11

    def test_send_user_cancelled_action_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_user_cancelled_action("interrupt_agent")

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.user_cancelled_action"
        assert telemetry_events[0]["properties"]["action"] == "interrupt_agent"

    def test_send_auto_compact_triggered_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_auto_compact_triggered()

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.auto_compact_triggered"

    def test_send_slash_command_used_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_slash_command_used("help", "builtin")
        client.send_slash_command_used("my_skill", "skill")

        assert len(telemetry_events) == 2
        assert telemetry_events[0]["event_name"] == "vibe.slash_command_used"
        assert telemetry_events[0]["properties"]["command"] == "help"
        assert telemetry_events[0]["properties"]["command_type"] == "builtin"
        assert telemetry_events[1]["properties"]["command"] == "my_skill"
        assert telemetry_events[1]["properties"]["command_type"] == "skill"

    def test_send_new_session_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_new_session(
            has_agents_md=True,
            nb_skills=2,
            nb_mcp_servers=1,
            nb_models=3,
            entrypoint="cli",
            terminal_emulator="vscode",
        )

        assert len(telemetry_events) == 1
        event_name = telemetry_events[0]["event_name"]
        assert event_name == "vibe.new_session"
        properties = telemetry_events[0]["properties"]
        assert properties["has_agents_md"] is True
        assert properties["nb_skills"] == 2
        assert properties["nb_mcp_servers"] == 1
        assert properties["nb_models"] == 3
        assert properties["entrypoint"] == "cli"
        assert properties["terminal_emulator"] == "vscode"
        assert "version" in properties
