from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server._session_resources import WorkspaceResource
from vibe.app_server.protocol import WorkspaceUntrustedConfigResponse
from vibe.cli.textual_ui.app import _UNTRUSTED_CONFIG_WARNING_SECTION
from vibe.cli.textual_ui.widgets.messages import WarningMessage
from vibe.utils.cache_store import FileSystemCacheStore

_MARKER = "Untrusted local config folders"


def _warnings(app) -> list[WarningMessage]:
    return [w for w in app.query(WarningMessage) if _MARKER in w._message]


def _acknowledged() -> set[str]:
    section = FileSystemCacheStore().read_section(_UNTRUSTED_CONFIG_WARNING_SECTION)
    dirs = section.get("dirs")
    return set(dirs) if isinstance(dirs, list) else set()


async def _wait_until(pilot, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


async def _wait_for_warning_and_ack(pilot, app, dirs: set[str]) -> None:
    """Wait for the warning to show and its acknowledgment to persist.

    The cache write runs in a worker after the warning mounts, so tearing down
    on the mount alone races the write and lets the next launch re-warn.
    """
    assert await _wait_until(pilot, lambda: bool(_warnings(app)))
    assert await _wait_until(pilot, lambda: dirs <= _acknowledged())


@pytest.fixture
def _stub_untrusted_config(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Force the startup check to report one untrusted config folder."""
    response = WorkspaceUntrustedConfigResponse(
        dirs=["/proj/.vibe"], settings_path="/home/.vibe/trusted_folders.toml"
    )
    stub = AsyncMock(return_value=response)
    monkeypatch.setattr(WorkspaceResource, "untrusted_config_dirs", stub)
    return stub


@pytest.mark.asyncio
async def test_untrusted_config_warning_shows_on_first_launch(
    _stub_untrusted_config: AsyncMock,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        assert await _wait_until(pilot, lambda: bool(_warnings(app)))
        assert "/proj/.vibe" in _warnings(app)[0]._message


@pytest.mark.asyncio
async def test_untrusted_config_warning_suppressed_after_acknowledged(
    _stub_untrusted_config: AsyncMock,
) -> None:
    # First launch records the folder as already surfaced (shared tmp vibe home).
    first = build_test_vibe_app()
    async with first.run_test() as pilot:
        await _wait_for_warning_and_ack(pilot, first, {"/proj/.vibe"})

    # Second launch with the same untrusted folder must not nag again.
    calls_before = _stub_untrusted_config.call_count
    second = build_test_vibe_app()
    async with second.run_test() as pilot:
        assert await _wait_until(
            pilot, lambda: _stub_untrusted_config.call_count > calls_before
        )
        await pilot.pause(0.2)
        assert _warnings(second) == []


@pytest.mark.asyncio
async def test_untrusted_config_warning_reappears_for_new_folder(
    _stub_untrusted_config: AsyncMock,
) -> None:
    first = build_test_vibe_app()
    async with first.run_test() as pilot:
        assert await _wait_until(pilot, lambda: bool(_warnings(first)))

    # A newly untrusted folder is not yet acknowledged, so the warning returns.
    _stub_untrusted_config.return_value = WorkspaceUntrustedConfigResponse(
        dirs=["/proj/.vibe", "/proj/.agents"],
        settings_path="/home/.vibe/trusted_folders.toml",
    )
    second = build_test_vibe_app()
    async with second.run_test() as pilot:
        assert await _wait_until(pilot, lambda: bool(_warnings(second)))
        assert "/proj/.agents" in _warnings(second)[0]._message


@pytest.mark.asyncio
async def test_untrusted_config_warning_keeps_prior_project_acknowledgments(
    _stub_untrusted_config: AsyncMock,
) -> None:
    # Acknowledge project A's untrusted folder.
    first = build_test_vibe_app()
    async with first.run_test() as pilot:
        await _wait_for_warning_and_ack(pilot, first, {"/proj/.vibe"})

    # Project B has a different cwd-scoped set; acknowledging it must not
    # drop project A's path from the shared cache.
    _stub_untrusted_config.return_value = WorkspaceUntrustedConfigResponse(
        dirs=["/other/.vibe"], settings_path="/home/.vibe/trusted_folders.toml"
    )
    second = build_test_vibe_app()
    async with second.run_test() as pilot:
        await _wait_for_warning_and_ack(pilot, second, {"/other/.vibe"})

    # Returning to project A should stay suppressed.
    _stub_untrusted_config.return_value = WorkspaceUntrustedConfigResponse(
        dirs=["/proj/.vibe"], settings_path="/home/.vibe/trusted_folders.toml"
    )
    calls_before = _stub_untrusted_config.call_count
    third = build_test_vibe_app()
    async with third.run_test() as pilot:
        assert await _wait_until(
            pilot, lambda: _stub_untrusted_config.call_count > calls_before
        )
        await pilot.pause(0.2)
        assert _warnings(third) == []
