from __future__ import annotations

import asyncio
import time
from typing import Protocol

import pytest
from textual.app import Notification

from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_version_update_gateway import (
    FakeVersionUpdateGateway,
)
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.update_notifier import (
    UpdateCache,
    VersionUpdate,
    VersionUpdateGatewayCause,
    VersionUpdateGatewayError,
)
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.modes import AgentMode


async def _wait_for_notification(
    app: VibeApp, pilot, *, timeout: float = 1.0, interval: float = 0.05
) -> Notification:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while loop.time() < deadline:
        notifications = list(app._notifications)
        if notifications:
            return notifications[-1]
        await pilot.pause(interval)

    pytest.fail("Notification not displayed")


async def _assert_no_notifications(
    app: VibeApp, pilot, *, timeout: float = 1.0, interval: float = 0.05
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while loop.time() < deadline:
        if app._notifications:
            pytest.fail("Notification unexpectedly displayed")
        await pilot.pause(interval)

    assert not app._notifications


@pytest.fixture
def vibe_config_with_update_checks_enabled() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=True
    )


class VibeAppFactory(Protocol):
    def __call__(
        self,
        *,
        notifier: FakeVersionUpdateGateway,
        update_cache_repository: FakeUpdateCacheRepository | None = None,
        config: VibeConfig | None = None,
        initial_mode: AgentMode = AgentMode.DEFAULT,
        current_version: str = "0.1.0",
    ) -> VibeApp: ...


@pytest.fixture
def make_vibe_app(vibe_config_with_update_checks_enabled: VibeConfig) -> VibeAppFactory:
    update_cache_repository = FakeUpdateCacheRepository()

    def _make_app(
        *,
        notifier: FakeVersionUpdateGateway,
        update_cache_repository: FakeUpdateCacheRepository
        | None = update_cache_repository,
        config: VibeConfig | None = None,
        initial_mode: AgentMode = AgentMode.DEFAULT,
        current_version: str = "0.1.0",
    ) -> VibeApp:
        return VibeApp(
            config=config or vibe_config_with_update_checks_enabled,
            initial_mode=initial_mode,
            version_update_notifier=notifier,
            update_cache_repository=update_cache_repository,
            current_version=current_version,
        )

    return _make_app


@pytest.mark.asyncio
async def test_ui_displays_update_notification(make_vibe_app: VibeAppFactory) -> None:
    notifier = FakeVersionUpdateGateway(update=VersionUpdate(latest_version="0.2.0"))
    app = make_vibe_app(notifier=notifier)

    async with app.run_test() as pilot:
        notification = await _wait_for_notification(app, pilot, timeout=0.3)

    assert notification.severity == "information"
    assert notification.title == "Update available"
    assert (
        notification.message
        == '0.1.0 => 0.2.0\nRun "uv tool upgrade mistral-vibe" to update'
    )


@pytest.mark.asyncio
async def test_ui_does_not_display_update_notification_when_not_available(
    make_vibe_app: VibeAppFactory,
) -> None:
    notifier = FakeVersionUpdateGateway(update=None)
    app = make_vibe_app(notifier=notifier)

    async with app.run_test() as pilot:
        await _assert_no_notifications(app, pilot, timeout=0.3)
    assert notifier.fetch_update_calls == 1


@pytest.mark.asyncio
async def test_ui_displays_warning_toast_when_check_fails(
    make_vibe_app: VibeAppFactory,
) -> None:
    notifier = FakeVersionUpdateGateway(
        error=VersionUpdateGatewayError(cause=VersionUpdateGatewayCause.FORBIDDEN)
    )
    app = make_vibe_app(notifier=notifier)

    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        notifications = list(app._notifications)

    assert notifications
    warning = notifications[-1]
    assert warning.severity == "warning"
    assert "forbidden" in warning.message.lower()


@pytest.mark.asyncio
async def test_ui_does_not_invoke_gateway_nor_show_error_notification_when_update_checks_are_disabled(
    vibe_config_with_update_checks_enabled: VibeConfig, make_vibe_app: VibeAppFactory
) -> None:
    config = vibe_config_with_update_checks_enabled
    config.enable_update_checks = False
    notifier = FakeVersionUpdateGateway(update=VersionUpdate(latest_version="0.2.0"))
    app = make_vibe_app(notifier=notifier, config=config)

    async with app.run_test() as pilot:
        await _assert_no_notifications(app, pilot, timeout=0.3)

    assert notifier.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_ui_does_not_invoke_gateway_nor_show_update_notification_when_update_checks_are_disabled(
    vibe_config_with_update_checks_enabled: VibeConfig, make_vibe_app: VibeAppFactory
) -> None:
    config = vibe_config_with_update_checks_enabled
    config.enable_update_checks = False
    notifier = FakeVersionUpdateGateway(update=VersionUpdate(latest_version="0.2.0"))
    app = make_vibe_app(notifier=notifier, config=config)

    async with app.run_test() as pilot:
        await _assert_no_notifications(app, pilot, timeout=0.3)

    assert notifier.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_ui_does_not_show_toast_when_update_is_known_in_recent_cache_already(
    make_vibe_app: VibeAppFactory,
) -> None:
    timestamp_two_hours_ago = int(time.time()) - 2 * 60 * 60
    notifier = FakeVersionUpdateGateway(update=VersionUpdate(latest_version="0.2.0"))
    update_cache = UpdateCache(
        latest_version="0.2.0", stored_at_timestamp=timestamp_two_hours_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)
    app = make_vibe_app(
        notifier=notifier, update_cache_repository=update_cache_repository
    )

    async with app.run_test() as pilot:
        await _assert_no_notifications(app, pilot, timeout=0.3)

    assert notifier.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_ui_does_show_toast_when_cache_entry_is_too_old(
    make_vibe_app: VibeAppFactory,
) -> None:
    timestamp_two_days_ago = int(time.time()) - 2 * 24 * 60 * 60
    notifier = FakeVersionUpdateGateway(update=VersionUpdate(latest_version="0.2.0"))
    update_cache = UpdateCache(
        latest_version="0.2.0", stored_at_timestamp=timestamp_two_days_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)
    app = make_vibe_app(
        notifier=notifier, update_cache_repository=update_cache_repository
    )

    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        notifications = list(app._notifications)

    assert notifications
    notification = notifications[-1]
    assert notification.severity == "information"
    assert notification.title == "Update available"
    assert (
        notification.message
        == '0.1.0 => 0.2.0\nRun "uv tool upgrade mistral-vibe" to update'
    )
    assert notifier.fetch_update_calls == 1
