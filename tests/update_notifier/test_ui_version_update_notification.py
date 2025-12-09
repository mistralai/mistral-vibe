from __future__ import annotations

import asyncio
from typing import Protocol

import pytest
from textual.app import Notification

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.update_notifier.fake_version_update_gateway import (
    FakeVersionUpdateGateway,
)
from vibe.cli.update_notifier.version_update_gateway import (
    VersionUpdate,
    VersionUpdateGatewayCause,
    VersionUpdateGatewayError,
)
from vibe.core.config import SessionLoggingConfig, VibeConfig


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
        config: VibeConfig | None = None,
        auto_approve: bool = False,
        current_version: str = "0.1.0",
    ) -> VibeApp: ...


@pytest.fixture
def make_vibe_app(vibe_config_with_update_checks_enabled: VibeConfig) -> VibeAppFactory:
    def _make_app(
        *,
        notifier: FakeVersionUpdateGateway,
        config: VibeConfig | None = None,
        auto_approve: bool = False,
        current_version: str = "0.1.0",
    ) -> VibeApp:
        return VibeApp(
            config=config or vibe_config_with_update_checks_enabled,
            auto_approve=auto_approve,
            version_update_notifier=notifier,
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
