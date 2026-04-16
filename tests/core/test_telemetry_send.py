from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_tool import FakeTool, FakeToolArgs
from vibe.core.agent_loop import ToolDecision, ToolExecutionResponse
from vibe.core.llm.format import ResolvedToolCall
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.tools.base import BaseTool, ToolPermission
from vibe.core.types import Backend
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
    def test_send_telemetry_event_swallows_config_getter_value_error(self) -> None:
        def _raise_config_error() -> Any:
            raise ValueError("config not ready")

        client = TelemetryClient(config_getter=_raise_config_error)
        client.send_telemetry_event("vibe.test", {})

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
            "https://api.mistral.ai/v1/datalake/events",
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
            client_name="vscode",
            client_version="1.96.0",
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
        assert properties["client_name"] == "vscode"
        assert properties["client_version"] == "1.96.0"
        assert properties["terminal_emulator"] == "vscode"
        assert "version" in properties

    @pytest.mark.asyncio
    async def test_session_id_added_when_getter_provided(
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
        session_id = "test-session-uuid"
        client = TelemetryClient(
            config_getter=lambda: config, session_id_getter=lambda: session_id
        )
        mock_post = AsyncMock(return_value=MagicMock(status_code=204))
        client._client = MagicMock()
        client._client.post = mock_post
        client._client.aclose = AsyncMock()

        client.send_telemetry_event("vibe.test_event", {"key": "value"})
        await client.aclose()

        mock_post.assert_called_once_with(
            "https://api.mistral.ai/v1/datalake/events",
            json={
                "event": "vibe.test_event",
                "properties": {"session_id": session_id, "key": "value"},
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-test",
                "User-Agent": get_user_agent(Backend.MISTRAL),
            },
        )

    @pytest.mark.asyncio
    async def test_session_id_absent_when_no_getter(
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
            "https://api.mistral.ai/v1/datalake/events",
            json={"event": "vibe.test_event", "properties": {"key": "value"}},
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-test",
                "User-Agent": get_user_agent(Backend.MISTRAL),
            },
        )

    @pytest.mark.asyncio
    async def test_session_id_getter_reflects_latest_value(
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
        current_id = "first-session-id"
        client = TelemetryClient(
            config_getter=lambda: config, session_id_getter=lambda: current_id
        )
        mock_post = AsyncMock(return_value=MagicMock(status_code=204))
        client._client = MagicMock()
        client._client.post = mock_post
        client._client.aclose = AsyncMock()

        client.send_telemetry_event("vibe.test_event", {})
        current_id = "second-session-id"
        client.send_telemetry_event("vibe.test_event", {})
        await client.aclose()

        calls = mock_post.call_args_list
        assert calls[0].kwargs["json"]["properties"]["session_id"] == "first-session-id"
        assert (
            calls[1].kwargs["json"]["properties"]["session_id"] == "second-session-id"
        )

    def test_send_user_rating_feedback_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_user_rating_feedback(rating=2, model="mistral-large")

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.user_rating_feedback"
        properties = telemetry_events[0]["properties"]
        assert properties["rating"] == 2
        assert properties["model"] == "mistral-large"
        assert "version" in properties

    def test_send_user_rating_feedback_includes_correlation_id(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)
        client.last_correlation_id = "corr-abc-123"

        client.send_user_rating_feedback(rating=1, model="mistral-large")

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["correlation_id"] == "corr-abc-123"

    def test_send_user_rating_feedback_omits_correlation_id_when_none(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_user_rating_feedback(rating=1, model="mistral-large")

        assert len(telemetry_events) == 1
        assert "correlation_id" not in telemetry_events[0]

    def test_telemetry_url_custom_provider_config(self) -> None:
        from vibe.core.config import ProviderConfig
        from vibe.core.types import Backend

        custom_api_base = "https://api.custom.host/v2"

        config = build_test_vibe_config(
            enable_telemetry=True,
            providers=[
                ProviderConfig(
                    name="mistral",
                    api_base=custom_api_base,
                    api_key_env_var="MISTRAL_API_KEY",
                    backend=Backend.MISTRAL,
                )
            ],
        )
        client = TelemetryClient(config_getter=lambda: config)

        assert (
            client._get_telemetry_url(custom_api_base)
            == "https://api.custom.host/v1/datalake/events"
        )

    def test_telemetry_url_preserves_port_in_api_base(self) -> None:
        from vibe.core.config import ProviderConfig
        from vibe.core.types import Backend

        custom_api_base = "http://api.custom.host:8080/v1/"

        config = build_test_vibe_config(
            enable_telemetry=True,
            providers=[
                ProviderConfig(
                    name="mistral",
                    api_base=custom_api_base,
                    api_key_env_var="MISTRAL_API_KEY",
                    backend=Backend.MISTRAL,
                )
            ],
        )
        client = TelemetryClient(config_getter=lambda: config)

        assert (
            client._get_telemetry_url(custom_api_base)
            == "http://api.custom.host:8080/v1/datalake/events"
        )

    def test_telemetry_url_falls_back_to_default_when_api_base_malformed(self) -> None:
        from vibe.core.config import ProviderConfig
        from vibe.core.types import Backend

        config = build_test_vibe_config(
            enable_telemetry=True,
            providers=[
                ProviderConfig(
                    name="mistral",
                    api_base="not-a-valid-url",
                    api_key_env_var="MISTRAL_API_KEY",
                    backend=Backend.MISTRAL,
                )
            ],
        )
        client = TelemetryClient(config_getter=lambda: config)

        assert (
            client._get_telemetry_url("not-a-valid-url")
            == "https://api.mistral.ai/v1/datalake/events"
        )

    def test_is_active_false_when_mistral_provider_exists_but_no_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        env_key = config.get_provider_for_model(
            config.get_active_model()
        ).api_key_env_var
        monkeypatch.delenv(env_key, raising=False)
        client = TelemetryClient(config_getter=lambda: config)

        assert client.is_active() is False

    def test_is_active_false_when_no_mistral_provider(self) -> None:
        from vibe.core.config import ProviderConfig

        config = build_test_vibe_config(
            enable_telemetry=True,
            providers=[
                ProviderConfig(
                    name="llamacpp",
                    api_base="http://127.0.0.1:8080/v1",
                    api_key_env_var="",
                )
            ],
        )
        client = TelemetryClient(config_getter=lambda: config)

        assert client.is_active() is False

    def test_is_active_false_when_config_getter_raises_value_error(self) -> None:
        def _raise_config_error() -> Any:
            raise ValueError("config not ready")

        client = TelemetryClient(config_getter=_raise_config_error)
        assert client.is_active() is False
