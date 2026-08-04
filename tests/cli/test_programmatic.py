from __future__ import annotations

from dataclasses import replace
from io import StringIO
import json
from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.mock_backend_factory import mock_backend_factory
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.local import ClientDescriptor, LocalHarnessOptions
from vibe.app_server.models import TeleportComplete
from vibe.app_server.protocol import ClientInfo, SessionOptions
from vibe.cli.programmatic import (
    OutputFormat,
    ProgrammaticLimitError,
    ProgrammaticOutput,
    run_programmatic,
)
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.types import Backend


def _options() -> LocalHarnessOptions:
    return LocalHarnessOptions(
        client=ClientDescriptor(
            info=ClientInfo(
                name="vibe_programmatic", version="test", entrypoint="programmatic"
            )
        ),
        session_options=SessionOptions(
            agent=BuiltinAgentName.AUTO_APPROVE,
            disabled_tools=["ask_user_question", "exit_plan_mode"],
            headless=True,
        ),
    )


def _use_runtime_config(
    monkeypatch: pytest.MonkeyPatch, config: VibeConfigSchema
) -> None:
    async def build_orchestrator(
        data: dict[str, object] | None = None, *, harness_files: HarnessFilesManager
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        del harness_files
        base = OverridesLayer(data=config.model_dump(mode="json"), name="base")
        session = OverridesLayer(data=data or {})
        return await ConfigOrchestrator.create(
            schema=VibeConfigSchema,
            layers=[base, session],
            default_layer_resolver=lambda: base,
        )

    monkeypatch.setattr(
        "vibe.app_server._runtime.build_default_orchestrator", build_orchestrator
    )


def test_streaming_output_uses_public_history_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    telemetry_events: list[dict],
) -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)

    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend(
            mock_llm_chunk(content="Decorators wrap functions.")
        ),
    ):
        result = run_programmatic(
            harness_options=_options(),
            prompt="Explain decorators",
            output_format=OutputFormat.STREAMING,
        )

    assert result is None
    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    messages = [entry for entry in entries if entry["type"] == "message"]
    assert [(entry["role"], entry["content"][0]["text"]) for entry in messages] == [
        ("user", "Explain decorators"),
        ("assistant", "Decorators wrap functions."),
    ]
    assert not any(entry["role"] == "system" for entry in messages)

    new_sessions = [
        event
        for event in telemetry_events
        if event.get("event_name") == "vibe.new_session"
    ]
    assert len(new_sessions) == 1
    assert new_sessions[0]["properties"]["agent_entrypoint"] == "programmatic"

    closed_sessions = [
        event
        for event in telemetry_events
        if event.get("event_name") == "vibe.session_closed"
    ]
    assert len(closed_sessions) == 1


def test_text_output_returns_last_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)

    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend([mock_llm_chunk(content="Understood.")]),
    ):
        result = run_programmatic(
            harness_options=_options(),
            prompt="Continue",
            output_format=OutputFormat.TEXT,
        )

    assert result == "Understood."


def test_json_teleport_output_includes_the_result_url() -> None:
    stream = StringIO()
    output = ProgrammaticOutput(OutputFormat.JSON, stream)

    output.consume_teleport(
        TeleportComplete(operation_id="teleport-1", url="https://vibe.example/run")
    )
    output.finalize([])

    assert json.loads(stream.getvalue()) == {
        "history": [],
        "teleportUrl": "https://vibe.example/run",
    }


def test_untrusted_workspace_warning_comes_from_app_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("project instructions", encoding="utf-8")
    monkeypatch.chdir(project)
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)

    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend([mock_llm_chunk(content="Done.")]),
    ):
        run_programmatic(harness_options=_options(), prompt="Continue")

    warning = capsys.readouterr().err
    assert str(project) in warning
    assert "AGENTS.md" in warning
    assert "--trust" in warning


def test_conversation_limits_cross_the_public_turn_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)
    options = _options()
    options = replace(
        options,
        session_options=options.session_options.model_copy(update={"max_turns": 0}),
    )

    with mock_backend_factory(
        Backend.MISTRAL, lambda provider, **kwargs: FakeBackend()
    ):
        with pytest.raises(ProgrammaticLimitError, match="Turn limit"):
            run_programmatic(harness_options=options, prompt="Continue")


def test_teleport_flag_runs_normal_turn_when_vibe_code_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config(
        include_model_info=False,
        include_commit_signature=False,
        vibe_code_enabled=False,
    )
    _use_runtime_config(monkeypatch, config)

    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend([
            mock_llm_chunk(content="Normal response.")
        ]),
    ):
        result = run_programmatic(
            harness_options=_options(),
            prompt="Hello",
            output_format=OutputFormat.TEXT,
            teleport=True,
        )

    assert result == "Normal response."
