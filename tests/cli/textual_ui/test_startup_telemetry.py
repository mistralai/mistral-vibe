from __future__ import annotations

from importlib.util import cache_from_source
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.session import AppServerSession
from vibe.cli.textual_ui.app import StartupOptions


@pytest.mark.asyncio
async def test_record_tui_displayed_is_idempotent() -> None:
    app = build_test_vibe_app()

    assert app._tui_displayed_monotonic is None

    app._record_tui_displayed()
    first = app._tui_displayed_monotonic
    assert first is not None

    app._record_tui_displayed()
    assert app._tui_displayed_monotonic is first


@pytest.mark.asyncio
async def test_watch_init_completion_none_first_frame_when_tui_not_displayed() -> None:
    app = build_test_vibe_app()

    runtime = MagicMock()
    runtime.ready = False
    runtime.wait_until_ready = AsyncMock()
    runtime.session_init_duration_ms = 42
    telemetry = MagicMock()
    telemetry.record = MagicMock()

    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app_server.resources.telemetry = telemetry
    app._app_server = app_server
    app._tui_displayed_monotonic = None

    with (
        patch.object(app, "_show_mcp_discovery_failures", MagicMock()),
        patch.object(app, "_show_mcp_auth_required_notice", AsyncMock()),
        patch.object(app, "_ensure_loading_widget", AsyncMock()),
        patch.object(app, "_remove_loading_widget", AsyncMock()),
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._watch_init_completion()

    assert app._startup_telemetry_sent is True
    assert telemetry.record.call_count == 1
    ((name, payload), _) = telemetry.record.call_args
    assert name == "vibe.startup"
    assert payload["session_init_duration_ms"] == 42
    assert payload["first_frame_duration_ms"] is None
    assert isinstance(payload["agent_ready_duration_ms"], int)
    assert payload["has_initial_prompt"] is False
    assert payload["teleport_on_start"] is False
    assert payload["show_resume_picker"] is False
    assert payload["is_resuming_session"] is False
    assert payload["prompt_for_workspace_trust"] is False
    assert payload["is_cold_start"] in (True, False, None)


@pytest.mark.asyncio
async def test_watch_init_completion_emits_startup_telemetry_once() -> None:
    app = build_test_vibe_app()

    runtime = MagicMock()
    runtime.ready = False
    runtime.wait_until_ready = AsyncMock()
    runtime.session_init_duration_ms = 42
    telemetry = MagicMock()
    telemetry.record = MagicMock()

    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app_server.resources.telemetry = telemetry
    app._app_server = app_server
    start = 1000.0
    app._tui_displayed_monotonic = start + 0.25

    assert app._startup_telemetry_sent is False

    with (
        patch("vibe.cli.textual_ui.app.PROCESS_START_MONOTONIC", start),
        patch("vibe.cli.textual_ui.app.time.monotonic", return_value=start + 0.5),
        patch.object(app, "_show_mcp_discovery_failures", MagicMock()),
        patch.object(app, "_show_mcp_auth_required_notice", AsyncMock()),
        patch.object(app, "_ensure_loading_widget", AsyncMock()),
        patch.object(app, "_remove_loading_widget", AsyncMock()),
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._watch_init_completion()
        await app._watch_init_completion()

    assert app._startup_telemetry_sent is True
    assert telemetry.record.call_count == 1
    ((name, payload), _) = telemetry.record.call_args
    assert name == "vibe.startup"
    assert payload["session_init_duration_ms"] == 42
    assert payload["first_frame_duration_ms"] == 250
    assert payload["agent_ready_duration_ms"] == 500
    assert payload["agent_ready_duration_ms"] >= payload["first_frame_duration_ms"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", [ProtocolErrorCode.CONFLICT, ProtocolErrorCode.NOT_FOUND]
)
async def test_superseded_watch_defers_post_init_notices_to_resume(
    code: ProtocolErrorCode,
) -> None:
    app = build_test_vibe_app()

    runtime = MagicMock()
    runtime.ready = False
    runtime.wait_until_ready = AsyncMock(
        side_effect=AppServerResponseError(
            ProtocolError(code=code, message="superseded by resume")
        )
    )
    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app._app_server = app_server

    with (
        patch.object(app, "_show_mcp_discovery_failures", MagicMock()) as discovery,
        patch.object(app, "_show_mcp_auth_required_notice", AsyncMock()) as auth,
        patch.object(app, "_send_startup_telemetry_once", MagicMock()),
        patch.object(app, "_ensure_loading_widget", AsyncMock()),
        patch.object(app, "_remove_loading_widget", AsyncMock()),
        patch.object(app, "_refresh_banner", MagicMock()),
        patch.object(app, "_mount_and_scroll", AsyncMock()) as mount,
    ):
        # A resume supersedes the fresh session's readiness watch — CONFLICT while
        # the reservation is held, or NOT_FOUND once the root is rebound. Either way
        # the watch returns early: no fatal-init state, no error mounted, notices
        # deferred to the resume path.
        await app._watch_init_completion()
        assert app._fatal_init_error is False
        mount.assert_not_awaited()
        assert app._post_init_notices_shown is False
        discovery.assert_not_called()
        auth.assert_not_awaited()

        # The resume path surfaces them instead, exactly once even if called twice.
        await app._show_post_init_notices_once()
        await app._show_post_init_notices_once()

    assert app._post_init_notices_shown is True
    discovery.assert_called_once_with()
    auth.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_watch_init_completion_first_frame_invariant_when_tui_displayed() -> None:
    app = build_test_vibe_app()

    runtime = MagicMock()
    runtime.ready = False
    runtime.wait_until_ready = AsyncMock()
    runtime.session_init_duration_ms = 42
    telemetry = MagicMock()
    telemetry.record = MagicMock()

    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app_server.resources.telemetry = telemetry
    app._app_server = app_server

    start = 1000.0
    app._tui_displayed_monotonic = start + 0.25

    with (
        patch("vibe.cli.textual_ui.app.PROCESS_START_MONOTONIC", start),
        patch("vibe.cli.textual_ui.app.time.monotonic", return_value=start + 0.5),
        patch.object(app, "_show_mcp_discovery_failures", MagicMock()),
        patch.object(app, "_show_mcp_auth_required_notice", AsyncMock()),
        patch.object(app, "_ensure_loading_widget", AsyncMock()),
        patch.object(app, "_remove_loading_widget", AsyncMock()),
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._watch_init_completion()

    assert app._startup_telemetry_sent is True
    assert telemetry.record.call_count == 1
    ((name, payload), _) = telemetry.record.call_args
    assert name == "vibe.startup"
    assert payload["session_init_duration_ms"] == 42
    first_frame = payload["first_frame_duration_ms"]
    agent_ready = payload["agent_ready_duration_ms"]
    assert isinstance(first_frame, int)
    assert first_frame == 250
    assert isinstance(agent_ready, int)
    assert agent_ready == 500
    assert agent_ready >= first_frame


@pytest.mark.asyncio
async def test_startup_telemetry_includes_startup_context() -> None:
    app = build_test_vibe_app(
        startup=StartupOptions(
            initial_prompt="continue the work",
            teleport_on_start=True,
            show_resume_picker=True,
            is_resuming_session=True,
            prompt_for_workspace_trust=True,
        )
    )

    runtime = MagicMock()
    runtime.session_init_duration_ms = 42
    telemetry = MagicMock()
    telemetry.record = MagicMock()

    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app_server.resources.telemetry = telemetry
    app._app_server = app_server

    app._show_resume_picker = False
    app._send_startup_telemetry_once()

    assert telemetry.record.call_count == 1
    ((name, payload), _) = telemetry.record.call_args
    assert name == "vibe.startup"
    assert payload["has_initial_prompt"] is True
    assert payload["teleport_on_start"] is True
    assert payload["show_resume_picker"] is True
    assert payload["is_resuming_session"] is True
    assert payload["prompt_for_workspace_trust"] is True


@pytest.mark.asyncio
async def test_startup_telemetry_preserves_resume_picker_after_pre_app_resolution() -> (
    None
):
    app = build_test_vibe_app(
        startup=StartupOptions(
            show_resume_picker=False,
            prompt_for_workspace_trust=False,
            startup_show_resume_picker=True,
            startup_prompt_for_workspace_trust=True,
        )
    )

    runtime = MagicMock()
    runtime.session_init_duration_ms = 42
    telemetry = MagicMock()
    telemetry.record = MagicMock()

    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app_server.resources.telemetry = telemetry
    app._app_server = app_server

    app._send_startup_telemetry_once()

    ((_, payload), _) = telemetry.record.call_args
    assert payload["show_resume_picker"] is True
    assert payload["prompt_for_workspace_trust"] is True
    assert app._show_resume_picker is False


@pytest.mark.asyncio
async def test_startup_telemetry_false_when_prompt_requested_but_not_shown() -> None:
    app = build_test_vibe_app(
        startup=StartupOptions(
            show_resume_picker=False,
            prompt_for_workspace_trust=False,
            startup_show_resume_picker=False,
            startup_prompt_for_workspace_trust=False,
        )
    )

    runtime = MagicMock()
    runtime.session_init_duration_ms = 42
    telemetry = MagicMock()
    telemetry.record = MagicMock()

    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app_server.resources.telemetry = telemetry
    app._app_server = app_server

    app._send_startup_telemetry_once()

    ((_, payload), _) = telemetry.record.call_args
    assert payload["show_resume_picker"] is False
    assert payload["prompt_for_workspace_trust"] is False


async def _emit_startup_telemetry(app) -> dict[str, object]:
    runtime = MagicMock()
    runtime.ready = True
    runtime.wait_until_ready = AsyncMock()
    runtime.session_init_duration_ms = 42
    telemetry = MagicMock()
    telemetry.record = MagicMock()

    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app_server.resources.telemetry = telemetry
    app._app_server = app_server

    with (
        patch.object(app, "_show_mcp_discovery_failures", MagicMock()),
        patch.object(app, "_show_mcp_auth_required_notice", AsyncMock()),
        patch.object(app, "_remove_loading_widget", AsyncMock()),
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._watch_init_completion()

    assert telemetry.record.call_count == 1
    ((name, payload), _) = telemetry.record.call_args
    assert name == "vibe.startup"
    return payload


@pytest.mark.asyncio
async def test_startup_telemetry_is_cold_start_true() -> None:
    app = build_test_vibe_app()
    with patch.object(app, "_is_cold_start", return_value=True):
        payload = await _emit_startup_telemetry(app)
    assert payload["is_cold_start"] is True


@pytest.mark.asyncio
async def test_startup_telemetry_is_cold_start_false() -> None:
    app = build_test_vibe_app()
    with patch.object(app, "_is_cold_start", return_value=False):
        payload = await _emit_startup_telemetry(app)
    assert payload["is_cold_start"] is False


@pytest.mark.asyncio
async def test_startup_telemetry_is_cold_start_none() -> None:
    app = build_test_vibe_app()
    with patch.object(app, "_is_cold_start", return_value=None):
        payload = await _emit_startup_telemetry(app)
    assert payload["is_cold_start"] is None


@pytest.mark.asyncio
async def test_is_cold_start_true_when_pyc_written_this_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_test_vibe_app()
    source = tmp_path / "app.py"
    source.write_text("", encoding="utf-8")
    pyc = Path(cache_from_source(str(source)))
    pyc.parent.mkdir(parents=True, exist_ok=True)
    pyc.write_text("", encoding="utf-8")
    monkeypatch.setattr("vibe.cli.textual_ui.app.__file__", str(source))
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.PROCESS_START_WALLCLOCK", pyc.stat().st_mtime - 1
    )
    monkeypatch.setattr("sys.dont_write_bytecode", False)
    assert app._is_cold_start() is True


@pytest.mark.asyncio
async def test_is_cold_start_false_when_pyc_predates_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_test_vibe_app()
    source = tmp_path / "app.py"
    source.write_text("", encoding="utf-8")
    pyc = Path(cache_from_source(str(source)))
    pyc.parent.mkdir(parents=True, exist_ok=True)
    pyc.write_text("", encoding="utf-8")
    monkeypatch.setattr("vibe.cli.textual_ui.app.__file__", str(source))
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.PROCESS_START_WALLCLOCK", pyc.stat().st_mtime + 100
    )
    monkeypatch.setattr("sys.dont_write_bytecode", False)
    assert app._is_cold_start() is False


@pytest.mark.asyncio
async def test_is_cold_start_none_when_bytecode_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_test_vibe_app()
    monkeypatch.setattr("sys.dont_write_bytecode", True)
    assert app._is_cold_start() is None


@pytest.mark.asyncio
async def test_is_cold_start_none_when_pyc_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_test_vibe_app()
    source = tmp_path / "app.py"
    source.write_text("", encoding="utf-8")
    monkeypatch.setattr("vibe.cli.textual_ui.app.__file__", str(source))
    monkeypatch.setattr("sys.dont_write_bytecode", False)
    assert app._is_cold_start() is None
