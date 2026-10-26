from __future__ import annotations

import asyncio
import platform
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import build_test_vibe_config
from vibe import __version__
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.telemetry.build_metadata import (
    build_base_metadata,
    build_request_metadata,
)
from vibe.core.telemetry.send import (
    TelemetryClient,
    send_unified_subagent_tool_call_finished,
    send_unified_tool_call_finished,
)
from vibe.core.telemetry.types import (
    AttachmentKind,
    ExperimentAssignment,
    LaunchContext,
    TelemetryRequestMetadata,
    TerminalEmulator,
)
from vibe.core.types import Backend
from vibe.core.utils import get_platform_id, get_platform_version
from vibe.utils.http import get_user_agent

_original_send_telemetry_event = TelemetryClient.send_telemetry_event


def _run_telemetry_tasks() -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.close()


def _expected_system_metadata(
    terminal_emulator: TerminalEmulator | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "os": get_platform_id(),
        "arch": platform.machine().lower(),
        "version": __version__,
    }
    if os_version := get_platform_version():
        metadata["os_version"] = os_version
    if terminal_emulator is not None:
        metadata["terminal_emulator"] = terminal_emulator
    return metadata


def _assert_system_metadata(
    properties: dict[str, Any], terminal_emulator: TerminalEmulator | None = None
) -> None:
    assert properties["os"] == get_platform_id()
    assert properties["arch"] == platform.machine().lower()
    assert properties["version"] == __version__
    if os_version := get_platform_version():
        assert properties["os_version"] == os_version
    else:
        assert "os_version" not in properties
    if terminal_emulator is None:
        assert "terminal_emulator" not in properties
        return
    assert properties["terminal_emulator"] == terminal_emulator


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
        env_key = config.get_active_provider().api_key_env_var
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
        env_key = config.get_active_provider().api_key_env_var
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
        env_key = config.get_active_provider().api_key_env_var
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
            json={
                "event": "vibe.test_event",
                "properties": {**_expected_system_metadata(), "key": "value"},
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-test",
                "User-Agent": get_user_agent(Backend.MISTRAL),
            },
        )

    def test_send_unified_subagent_tool_call_finished_uses_safe_dimensions(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        """*Prepare*: A telemetry client with event collection enabled.
        *Do*: Record a successful Unified subagent spawn.
        *Assert*: Only the approved bounded dimensions are emitted, including the
        client-level ``harness_backend`` tag.
        """
        # Prepare
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(
            config_getter=lambda: config, harness_backend=ExperimentSurface.UNIFIED
        )

        # Do
        send_unified_subagent_tool_call_finished(
            client,
            operation="spawn",
            outcome="success",
            model="mistral-vibe-cli-latest",
            caller_agent_path="/root",
            profile_source="vibe_profile",
        )

        # Assert
        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.tool_call_finished"
        properties = telemetry_events[0]["properties"]
        assert properties == properties | {
            "tool_name": "subagent.spawn",
            "status": "success",
            "decision": None,
            "approval_type": None,
            "agent_profile_name": None,
            "model": "mistral-vibe-cli-latest",
            "nb_files_created": 0,
            "nb_files_modified": 0,
            "file_extension": None,
            "message_id": None,
            "harness_backend": "unified",
            "subagent_operation": "spawn",
            "subagent_outcome": "success",
            "subagent_depth": 1,
            "subagent_profile_source": "vibe_profile",
        }
        assert "agent_name" not in properties
        assert "agent_type" not in properties
        assert "child_session_id" not in properties
        assert "prompt" not in properties
        assert "caller_agent_path" not in properties

    @pytest.mark.parametrize(
        ("caller_agent_path", "depth"),
        [("/root", 1), ("/root/research", 2), ("/root/research/check", 3)],
    )
    def test_send_unified_subagent_tool_call_finished_derives_depth_from_caller_path(
        self, telemetry_events: list[dict[str, Any]], caller_agent_path: str, depth: int
    ) -> None:
        """*Prepare*: A telemetry client with event collection enabled.
        *Do*: Record a subagent operation called by an agent at a given path.
        *Assert*: ``subagent_depth`` is the depth of the level acted on (the
        caller's depth + 1, root = 0) and the path itself is not emitted.
        """
        # Prepare
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(
            config_getter=lambda: config, harness_backend=ExperimentSurface.UNIFIED
        )

        # Do
        send_unified_subagent_tool_call_finished(
            client,
            operation="list_configs",
            outcome="success",
            model="mistral-vibe-cli-latest",
            caller_agent_path=caller_agent_path,
        )

        # Assert
        properties = telemetry_events[0]["properties"]
        assert properties["subagent_depth"] == depth
        assert properties["tool_name"] == "subagent.list_configs"
        assert properties["subagent_operation"] == "list_configs"
        assert caller_agent_path not in repr(properties)

    @pytest.mark.parametrize("caller_agent_path", ["", "/", "root/child"])
    def test_send_unified_subagent_tool_call_finished_rejects_non_absolute_path(
        self, telemetry_events: list[dict[str, Any]], caller_agent_path: str
    ) -> None:
        """*Prepare*: A telemetry client with event collection enabled.
        *Do*: Record a subagent operation with a malformed caller path.
        *Assert*: It raises instead of emitting a made-up depth.
        """
        # Prepare
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(
            config_getter=lambda: config, harness_backend=ExperimentSurface.UNIFIED
        )

        # Do / Assert
        with pytest.raises(ValueError, match="not an absolute agent path"):
            send_unified_subagent_tool_call_finished(
                client,
                operation="list",
                outcome="success",
                model="mistral-vibe-cli-latest",
                caller_agent_path=caller_agent_path,
            )
        assert telemetry_events == []

    def test_send_unified_tool_call_finished_carries_decision_and_approval_type(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        """*Prepare*: A telemetry client tagged unified.
        *Do*: Record a unified tool call with decision=execute, approval_type=ask.
        *Assert*: The event carries the actual decision and approval_type.
        """
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(
            config_getter=lambda: config, harness_backend=ExperimentSurface.UNIFIED
        )

        send_unified_tool_call_finished(
            client,
            tool_name="write_file",
            status="success",
            model="mistral-vibe-cli-latest",
            agent_profile_name="default",
            decision="execute",
            approval_type="ask",
            approval_source="user",
        )

        assert len(telemetry_events) == 1
        props = telemetry_events[0]["properties"]
        assert props["decision"] == "execute"
        assert props["approval_type"] == "ask"
        assert props["approval_source"] == "user"

    def test_harness_backend_rides_every_event_without_experiments(self) -> None:
        """*Prepare*: A client tagged legacy with no experiment snapshot.
        *Do*: Build the base metadata every event carries.
        *Assert*: ``harness_backend`` is present even though experiment
        segmentation is entirely absent.
        """
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(
            config_getter=lambda: config, harness_backend=ExperimentSurface.LEGACY
        )

        metadata = client.build_client_event_metadata()

        assert metadata["harness_backend"] == "legacy"
        assert "experiment_attributes" not in metadata

    def test_harness_backend_absent_when_client_is_untagged(self) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        assert "harness_backend" not in client.build_client_event_metadata()

    def test_experiment_attributes_serialize_nested_enums_as_strings(self) -> None:
        """*Prepare*: A client whose attribute snapshot carries enum fields.
        *Do*: Build the base metadata every event carries.
        *Assert*: Nested enums (harness, terminal_emulator) are plain strings,
        not Python enum objects — a clean wire payload and clean debug logs.
        """
        from vibe.core.experiments.models import ExperimentAttributes

        config = build_test_vibe_config(enable_telemetry=True)
        attributes = ExperimentAttributes(
            entrypoint="cli",
            harness=ExperimentSurface.UNIFIED,
            agent_version="1.2.3",
            os="darwin",
            terminal_emulator=TerminalEmulator.GHOSTTY,
        )
        client = TelemetryClient(
            config_getter=lambda: config,
            experiment_attributes_getter=lambda: attributes,
        )

        emitted = client.build_client_event_metadata()["experiment_attributes"]

        assert emitted["harness"] == "unified"
        assert not isinstance(emitted["harness"], ExperimentSurface)
        assert emitted["terminal_emulator"] == "ghostty"

    def test_send_auto_compact_triggered_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_auto_compact_triggered(
            nb_context_tokens_before=123, auto_compact_threshold=100, status="success"
        )

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.auto_compact_triggered"
        assert telemetry_events[0]["properties"] == {
            **_expected_system_metadata(),
            "nb_context_tokens_before": 123,
            "auto_compact_threshold": 100,
            "status": "success",
        }

    def test_send_compaction_failed_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_compaction_failed(reason="tool_call")

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.compaction_failed"
        assert telemetry_events[0]["properties"] == {
            **_expected_system_metadata(),
            "reason": "tool_call",
        }

    def test_send_compaction_failed_includes_session_ids(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_compaction_failed(
            reason="empty_summary",
            session_id="session-id",
            parent_session_id="parent-session-id",
        )

        assert len(telemetry_events) == 1
        properties = telemetry_events[0]["properties"]
        assert properties["reason"] == "empty_summary"
        assert properties["session_id"] == "session-id"
        assert properties["parent_session_id"] == "parent-session-id"

    def test_send_teleport_completed_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_teleport_completed(push_required=True, nb_session_messages=4)

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.teleport_completed"
        assert telemetry_events[0]["properties"] == {
            **_expected_system_metadata(),
            "push_required": True,
            "nb_session_messages": 4,
            "context_summary": "skipped",
            "context_summary_chars": None,
        }

    def test_send_teleport_completed_payload_includes_project_picker_context(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_teleport_completed(
            push_required=True,
            nb_session_messages=4,
            project_picker={
                "project_picker_shown": True,
                "project_selection_source": "selected_existing",
                "project_candidate_count_loaded": 3,
                "project_multi_repo_match_count": 1,
                "saved_project_link_cleared": False,
                "project_repo_remote_changed": False,
            },
        )

        assert telemetry_events[0]["event_name"] == "vibe.teleport_completed"
        assert {
            "project_picker_shown": True,
            "project_selection_source": "selected_existing",
            "project_candidate_count_loaded": 3,
            "project_multi_repo_match_count": 1,
            "saved_project_link_cleared": False,
            "project_repo_remote_changed": False,
        }.items() <= telemetry_events[0]["properties"].items()

    def test_send_teleport_failed_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_teleport_failed(
            stage="push",
            error_class="ServiceTeleportError",
            push_required=True,
            nb_session_messages=4,
        )

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.teleport_failed"
        assert telemetry_events[0]["properties"] == {
            **_expected_system_metadata(),
            "stage": "push",
            "error_class": "ServiceTeleportError",
            "push_required": True,
            "nb_session_messages": 4,
            "context_summary": "skipped",
            "context_summary_chars": None,
        }

    def test_send_teleport_failed_payload_includes_error_details(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_teleport_failed(
            stage="workflow_start",
            error_class="ServiceTeleportError",
            push_required=False,
            nb_session_messages=4,
            error_details={"failure_kind": "http_error", "http_status_code": 502},
        )

        assert telemetry_events[0]["event_name"] == "vibe.teleport_failed"
        assert telemetry_events[0]["properties"] == {
            **_expected_system_metadata(),
            "stage": "workflow_start",
            "error_class": "ServiceTeleportError",
            "push_required": False,
            "nb_session_messages": 4,
            "context_summary": "skipped",
            "context_summary_chars": None,
            "failure_kind": "http_error",
            "http_status_code": 502,
        }

    def test_send_remote_project_configured_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_remote_project_configured(
            outcome="configured",
            project_picker={
                "project_picker_shown": True,
                "project_selection_source": "selected_existing",
                "project_candidate_count_loaded": 2,
                "project_multi_repo_match_count": 0,
                "saved_project_link_cleared": False,
                "project_repo_remote_changed": False,
            },
        )

        assert telemetry_events[0]["event_name"] == "vibe.remote_project_configured"
        assert telemetry_events[0]["properties"] == {
            **_expected_system_metadata(),
            "outcome": "configured",
            "project_picker_shown": True,
            "project_selection_source": "selected_existing",
            "project_candidate_count_loaded": 2,
            "project_multi_repo_match_count": 0,
            "saved_project_link_cleared": False,
            "project_repo_remote_changed": False,
        }

    def test_send_new_session_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(
            config_getter=lambda: config,
            launch_context=LaunchContext(
                agent_entrypoint="cli",
                agent_version=__version__,
                client_name="vscode",
                client_version="1.96.0",
                terminal_emulator=TerminalEmulator.VSCODE,
            ),
        )

        client.send_new_session(
            has_agents_md=True,
            nb_skills=2,
            nb_mcp_servers=1,
            nb_models=3,
            has_outside_workdir_deny=False,
            nb_post_agent_scoped_hooks=2,
            nb_post_agent_main_hooks=1,
        )

        assert len(telemetry_events) == 1
        event_name = telemetry_events[0]["event_name"]
        assert event_name == "vibe.new_session"
        properties = telemetry_events[0]["properties"]
        assert properties["has_agents_md"] is True
        assert properties["nb_skills"] == 2
        assert properties["nb_mcp_servers"] == 1
        assert properties["nb_models"] == 3
        assert properties["nb_post_agent_scoped_hooks"] == 2
        assert properties["nb_post_agent_main_hooks"] == 1
        assert properties["entrypoint"] == "cli"
        assert properties["host_kind"] == "local"
        assert properties["client_name"] == "vscode"
        assert properties["client_version"] == "1.96.0"
        assert properties["terminal_emulator"] == "vscode"
        assert properties["has_outside_workdir_deny"] is False
        assert "experimental_bash_tool" not in properties
        assert "version" in properties
        assert type(properties["terminal_emulator"]) is str
        _assert_system_metadata(properties, TerminalEmulator.VSCODE)

    def test_send_new_session_metadata_includes_managed_shell_experiment(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        assignment = ExperimentAssignment(
            experiment_id="vibe_cli_managed_shell_tools",
            experiment_name="vibe_cli_managed_shell_tools",
            variation_name="managed",
            variation_id=1,
        )
        client = TelemetryClient(
            config_getter=lambda: config, experiments_getter=lambda: [assignment]
        )

        client.send_new_session(
            has_agents_md=False,
            nb_skills=0,
            nb_mcp_servers=0,
            nb_models=1,
            has_outside_workdir_deny=True,
            nb_post_agent_scoped_hooks=0,
            nb_post_agent_main_hooks=0,
        )

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.new_session"
        properties = telemetry_events[0]["properties"]
        assert "experimental_bash_tool" not in properties
        assert properties["has_outside_workdir_deny"] is True
        assert properties["experiments"] == {"vibe_cli_managed_shell_tools": "managed"}
        assert properties["experiment_assignments"] == [
            {
                "experiment_id": "vibe_cli_managed_shell_tools",
                "experiment_name": "vibe_cli_managed_shell_tools",
                "variation_name": "managed",
                "variation_id": 1,
            }
        ]

    @pytest.mark.asyncio
    async def test_send_session_closed_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            TelemetryClient, "send_telemetry_event", _original_send_telemetry_event
        )
        config = build_test_vibe_config(enable_telemetry=True)
        env_key = config.get_active_provider().api_key_env_var
        monkeypatch.setenv(env_key, "sk-test")
        client = TelemetryClient(
            config_getter=lambda: config,
            session_id_getter=lambda: "current-session",
            parent_session_id_getter=lambda: "current-parent-session",
            launch_context=LaunchContext(
                agent_entrypoint="cli",
                agent_version="1.0.0",
                client_name="vibe_cli",
                client_version="1.0.0",
                terminal_emulator=TerminalEmulator.VSCODE,
            ),
        )
        mock_post = AsyncMock(return_value=MagicMock(status_code=204))
        client._client = MagicMock()
        client._client.post = mock_post
        client._client.aclose = AsyncMock()

        client.send_session_closed()
        await client.aclose()

        mock_post.assert_called_once_with(
            "https://api.mistral.ai/v1/datalake/events",
            json={
                "event": "vibe.session_closed",
                "properties": {
                    **_expected_system_metadata(TerminalEmulator.VSCODE),
                    "agent_entrypoint": "cli",
                    "agent_version": "1.0.0",
                    "client_name": "vibe_cli",
                    "client_version": "1.0.0",
                    "session_id": "current-session",
                    "parent_session_id": "current-parent-session",
                },
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-test",
                "User-Agent": get_user_agent(Backend.MISTRAL),
            },
        )

    def test_build_base_metadata_includes_entrypoint_and_session(self) -> None:
        metadata = build_base_metadata(
            launch_context=LaunchContext(
                agent_entrypoint="cli",
                agent_version="1.0.0",
                client_name="vibe_cli",
                client_version="1.0.0",
                terminal_emulator=TerminalEmulator.VSCODE,
            ),
            session_id="session-123",
            parent_session_id="parent-session-456",
        )

        assert metadata == {
            **_expected_system_metadata(TerminalEmulator.VSCODE),
            "agent_entrypoint": "cli",
            "agent_version": "1.0.0",
            "client_name": "vibe_cli",
            "client_version": "1.0.0",
            "session_id": "session-123",
            "parent_session_id": "parent-session-456",
        }
        assert type(metadata["terminal_emulator"]) is str

    def test_build_request_metadata_includes_all_telemetry_metadata(self) -> None:
        metadata = build_request_metadata(
            launch_context=LaunchContext(
                agent_entrypoint="cli",
                agent_version="1.0.0",
                client_name="vibe_cli",
                client_version="1.0.0",
                terminal_emulator=TerminalEmulator.VSCODE,
            ),
            session_id="session-123",
            parent_session_id="parent-session-456",
            call_type="secondary_call",
            message_id="message-456",
            user_plan="Pro",
        )

        assert metadata == TelemetryRequestMetadata(
            **_expected_system_metadata(TerminalEmulator.VSCODE),
            agent_entrypoint="cli",
            agent_version="1.0.0",
            client_name="vibe_cli",
            client_version="1.0.0",
            session_id="session-123",
            parent_session_id="parent-session-456",
            call_source="vibe_code",
            call_type="secondary_call",
            host_kind="local",
            message_id="message-456",
            user_plan="Pro",
        )

    @pytest.mark.asyncio
    async def test_parent_session_id_added_when_getter_provided(
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
        client = TelemetryClient(
            config_getter=lambda: config,
            session_id_getter=lambda: "session-123",
            parent_session_id_getter=lambda: "parent-session-456",
            user_plan_getter=lambda: "Pro",
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
                "properties": {
                    **_expected_system_metadata(),
                    "session_id": "session-123",
                    "parent_session_id": "parent-session-456",
                    "user_plan": "Pro",
                    "key": "value",
                },
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-test",
                "User-Agent": get_user_agent(Backend.MISTRAL),
            },
        )

    @pytest.mark.asyncio
    async def test_session_id_added_when_getter_provided(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            TelemetryClient, "send_telemetry_event", _original_send_telemetry_event
        )
        config = build_test_vibe_config(enable_telemetry=True)
        env_key = config.get_active_provider().api_key_env_var
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
                "properties": {
                    **_expected_system_metadata(),
                    "session_id": session_id,
                    "key": "value",
                },
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
        env_key = config.get_active_provider().api_key_env_var
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
            json={
                "event": "vibe.test_event",
                "properties": {**_expected_system_metadata(), "key": "value"},
            },
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
        env_key = config.get_active_provider().api_key_env_var
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

    def test_send_auto_compact_triggered_overrides_session_metadata(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(
            config_getter=lambda: config,
            session_id_getter=lambda: "new-session-id",
            parent_session_id_getter=lambda: "new-parent-session-id",
        )

        client.send_auto_compact_triggered(
            nb_context_tokens_before=123,
            auto_compact_threshold=100,
            status="success",
            session_id="original-session-id",
            parent_session_id=None,
        )

        assert len(telemetry_events) == 1
        properties = telemetry_events[0]["properties"]
        assert properties["session_id"] == "original-session-id"
        assert properties["parent_session_id"] is None

    def test_send_ready_payload(self, telemetry_events: list[dict[str, Any]]) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_ready(init_duration_ms=1240)

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.ready"
        properties = telemetry_events[0]["properties"]
        assert properties["init_duration_ms"] == 1240
        _assert_system_metadata(properties)

    def test_send_request_sent_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_request_sent(
            model="codestral",
            nb_context_chars=1234,
            nb_context_messages=5,
            nb_prompt_chars=42,
            call_type="main_call",
        )

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.request_sent"
        properties = telemetry_events[0]["properties"]
        assert properties["model"] == "codestral"
        assert properties["nb_context_chars"] == 1234
        assert properties["nb_context_messages"] == 5
        assert properties["nb_prompt_chars"] == 42
        assert properties["call_source"] == "vibe_code"
        assert properties["call_type"] == "main_call"
        assert properties["message_id"] is None
        assert properties["attachment_counts"] == {}
        _assert_system_metadata(properties)

    def test_send_request_sent_payload_with_attachments(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_request_sent(
            model="codestral",
            nb_context_chars=1234,
            nb_context_messages=5,
            nb_prompt_chars=42,
            call_type="main_call",
            attachment_counts={AttachmentKind.IMAGE: 2},
        )

        assert len(telemetry_events) == 1
        properties = telemetry_events[0]["properties"]
        assert properties["attachment_counts"] == {"image": 2}

    def test_send_request_sent_payload_drops_zero_counts(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_request_sent(
            model="codestral",
            nb_context_chars=1234,
            nb_context_messages=5,
            nb_prompt_chars=42,
            call_type="main_call",
            attachment_counts={AttachmentKind.IMAGE: 0},
        )

        assert telemetry_events[0]["properties"]["attachment_counts"] == {}

    def test_send_tool_call_detected_payload(
        self, telemetry_events: list[dict[str, Any]]
    ) -> None:
        config = build_test_vibe_config(enable_telemetry=True)
        client = TelemetryClient(config_getter=lambda: config)

        client.send_tool_call_detected(
            model="glm-5-3",
            backend="mistral",
            api_style="openai",
            call_type="secondary_call",
            reasons=["empty_tool_call_identity"],
            nb_unusable_attempts=1,
            nb_dropped_tool_calls=1,
            status="success",
            message_id="message-1",
        )

        assert len(telemetry_events) == 1
        assert telemetry_events[0]["event_name"] == "vibe.tool_call.detected"
        properties = telemetry_events[0]["properties"]
        assert {
            key: properties[key]
            for key in (
                "model",
                "backend",
                "api_style",
                "call_source",
                "call_type",
                "message_id",
                "reasons",
                "nb_unusable_attempts",
                "nb_dropped_tool_calls",
                "status",
            )
        } == {
            "model": "glm-5-3",
            "backend": "mistral",
            "api_style": "openai",
            "call_source": "vibe_code",
            "call_type": "secondary_call",
            "message_id": "message-1",
            "reasons": ["empty_tool_call_identity"],
            "nb_unusable_attempts": 1,
            "nb_dropped_tool_calls": 1,
            "status": "success",
        }
        _assert_system_metadata(properties)

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
        env_key = config.get_active_provider().api_key_env_var
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
