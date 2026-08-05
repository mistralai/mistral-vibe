from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.session import AppServerSession


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
