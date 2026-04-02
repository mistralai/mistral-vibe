from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import time
from unittest.mock import patch

import pytest
from textual.app import Notification

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.messages import WhatsNewMessage
from vibe.cli.update_notifier import (
    Update,
    UpdateCache,
    UpdateCacheRepository,
    UpdateGateway,
    UpdateGatewayCause,
    UpdateGatewayError,
)
from vibe.core.config import VibeConfig

TEST_CURRENT_VERSION = "0.1.0"


@pytest.fixture
def build_update_test_app(
    vibe_config_with_update_checks_enabled: VibeConfig,
) -> Callable[..., VibeApp]:
    def _build(
        *,
        update_notifier: UpdateGateway | None = None,
        update_cache_repository: UpdateCacheRepository | None = None,
        config: VibeConfig | None = None,
        current_version: str = TEST_CURRENT_VERSION,
    ) -> VibeApp:
        return build_test_vibe_app(
            update_notifier=update_notifier,
            update_cache_repository=update_cache_repository,
            config=config or vibe_config_with_update_checks_enabled,
            current_version=current_version,
        )

    return _build


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
    return build_test_vibe_config(enable_update_checks=True)


@pytest.mark.asyncio
async def test_ui_displays_update_notification(
    build_update_test_app: Callable[..., VibeApp],
) -> None:
    notifier = FakeUpdateGateway(update=Update(latest_version="0.2.0"))
    app = build_update_test_app(update_notifier=notifier)

    async with app.run_test() as pilot:
        notification = await _wait_for_notification(app, pilot, timeout=0.3)

    assert notification.severity == "information"
    assert notification.title == "Update available"
    assert (
        notification.message
        == "0.1.0 => 0.2.0\nPlease update mistral-vibe with your package manager"
    )


@pytest.mark.asyncio
async def test_ui_does_not_display_update_notification_when_not_available(
    build_update_test_app: Callable[..., VibeApp],
) -> None:
    notifier = FakeUpdateGateway(update=None)
    app = build_update_test_app(update_notifier=notifier)

    async with app.run_test() as pilot:
        await _assert_no_notifications(app, pilot, timeout=0.3)
    assert notifier.fetch_update_calls == 1


@pytest.mark.asyncio
async def test_ui_displays_warning_toast_when_check_fails(
    build_update_test_app: Callable[..., VibeApp],
) -> None:
    notifier = FakeUpdateGateway(
        error=UpdateGatewayError(cause=UpdateGatewayCause.FORBIDDEN)
    )
    app = build_update_test_app(update_notifier=notifier)

    async with app.run_test() as pilot:
        await pilot.pause(0.3)
        notifications = list(app._notifications)

    assert notifications
    warning = notifications[-1]
    assert warning.severity == "warning"
    assert "forbidden" in warning.message.lower()


@pytest.mark.asyncio
async def test_ui_does_not_invoke_gateway_nor_show_error_notification_when_update_checks_are_disabled(
    build_update_test_app: Callable[..., VibeApp], vibe_config: VibeConfig
) -> None:
    notifier = FakeUpdateGateway(update=Update(latest_version="0.2.0"))
    app = build_update_test_app(update_notifier=notifier, config=vibe_config)

    async with app.run_test() as pilot:
        await _assert_no_notifications(app, pilot, timeout=0.3)

    assert notifier.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_ui_does_not_show_toast_when_update_is_known_in_recent_cache_already(
    build_update_test_app: Callable[..., VibeApp],
):
    timestamp_two_hours_ago = int(time.time()) - 2 * 60 * 60
    notifier = FakeUpdateGateway(update=Update(latest_version="0.2.0"))
    update_cache = UpdateCache(
        latest_version="0.2.0", stored_at_timestamp=timestamp_two_hours_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)
    app = build_update_test_app(
        update_notifier=notifier, update_cache_repository=update_cache_repository
    )

    async with app.run_test() as pilot:
        await _assert_no_notifications(app, pilot, timeout=0.3)

    assert notifier.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_ui_does_show_toast_when_cache_entry_is_too_old(
    build_update_test_app: Callable[..., VibeApp],
) -> None:
    timestamp_two_days_ago = int(time.time()) - 2 * 24 * 60 * 60
    notifier = FakeUpdateGateway(update=Update(latest_version="0.2.0"))
    update_cache = UpdateCache(
        latest_version="0.2.0", stored_at_timestamp=timestamp_two_days_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)
    app = build_update_test_app(
        update_notifier=notifier, update_cache_repository=update_cache_repository
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
        == "0.1.0 => 0.2.0\nPlease update mistral-vibe with your package manager"
    )
    assert notifier.fetch_update_calls == 1


async def _wait_for_whats_new_message(
    app: VibeApp, pilot, *, timeout: float = 1.0, interval: float = 0.05
) -> WhatsNewMessage:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    while loop.time() < deadline:
        try:
            message = app.query_one(WhatsNewMessage)
            if message:
                return message
        except Exception:
            pass
        await pilot.pause(interval)

    pytest.fail("WhatsNewMessage not displayed")


@pytest.mark.asyncio
async def test_ui_displays_whats_new_message_when_content_exists(
    build_update_test_app: Callable[..., VibeApp], tmp_path: Path
) -> None:
    notifier = FakeUpdateGateway(update=None)
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version=None,
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)
    app = build_update_test_app(
        update_notifier=notifier,
        update_cache_repository=repository,
        current_version="1.0.0",
    )

    whats_new_content = "# What's New\n\n- Feature 1\n- Feature 2"
    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        whats_new_file = tmp_path / "whats_new.md"
        whats_new_file.write_text(whats_new_content)

        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            message = await _wait_for_whats_new_message(app, pilot, timeout=0.5)

    assert message is not None
    assert message._content == whats_new_content
    assert repository.update_cache is not None
    assert repository.update_cache.seen_whats_new_version == "1.0.0"


@pytest.mark.asyncio
async def test_ui_does_not_display_whats_new_when_seen_whats_new_version_matches(
    build_update_test_app: Callable[..., VibeApp], tmp_path: Path
) -> None:
    notifier = FakeUpdateGateway(update=None)
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version="1.0.0",
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)
    app = build_update_test_app(
        update_notifier=notifier,
        update_cache_repository=repository,
        current_version="1.0.0",
    )

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        whats_new_file = tmp_path / "whats_new.md"
        whats_new_file.write_text("# What's New\n\n- Feature 1")

        async with app.run_test() as pilot:
            await pilot.pause(0.5)

    try:
        app.query_one(WhatsNewMessage)
        pytest.fail("WhatsNewMessage should not be displayed")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_ui_does_not_display_whats_new_when_file_is_empty(
    build_update_test_app: Callable[..., VibeApp], tmp_path: Path
) -> None:
    notifier = FakeUpdateGateway(update=None)
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version=None,
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)
    app = build_update_test_app(
        update_notifier=notifier,
        update_cache_repository=repository,
        current_version="1.0.0",
    )

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        whats_new_file = tmp_path / "whats_new.md"
        whats_new_file.write_text("")

        async with app.run_test() as pilot:
            await pilot.pause(0.5)

    try:
        app.query_one(WhatsNewMessage)
        pytest.fail("WhatsNewMessage should not be displayed")
    except Exception:
        pass  # Expected: message should not exist

    assert repository.update_cache is not None
    assert repository.update_cache.seen_whats_new_version == "1.0.0"


@pytest.mark.asyncio
async def test_ui_does_not_display_whats_new_when_file_does_not_exist(
    build_update_test_app: Callable[..., VibeApp], tmp_path: Path
) -> None:
    notifier = FakeUpdateGateway(update=None)
    cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version=None,
    )
    repository = FakeUpdateCacheRepository(update_cache=cache)
    app = build_update_test_app(
        update_notifier=notifier,
        update_cache_repository=repository,
        current_version="1.0.0",
    )

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        async with app.run_test() as pilot:
            await pilot.pause(0.5)

    try:
        app.query_one(WhatsNewMessage)
        pytest.fail("WhatsNewMessage should not be displayed")
    except Exception:
        pass  # Expected: message should not exist

    assert repository.update_cache is not None
    assert repository.update_cache.seen_whats_new_version == "1.0.0"


@pytest.mark.asyncio
async def test_ui_displays_success_notification_when_auto_update_succeeds(
    build_update_test_app: Callable[..., VibeApp],
) -> None:
    config = build_test_vibe_config(enable_update_checks=True, enable_auto_update=True)
    notifier = FakeUpdateGateway(update=Update(latest_version="0.2.0"))

    with patch("vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["true"]):
        app = build_update_test_app(update_notifier=notifier, config=config)

        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            notifications = list(app._notifications)

    assert notifications, "No notifications displayed"
    notification = notifications[-1]
    assert notification.severity == "information"
    assert notification.title == "Update successful"
    assert (
        notification.message
        == "0.1.0 => 0.2.0\nVibe was updated successfully. Please restart to use the new version."
    )


@pytest.mark.asyncio
async def test_ui_displays_update_notification_when_auto_update_fails(
    build_update_test_app: Callable[..., VibeApp],
) -> None:
    config = build_test_vibe_config(enable_update_checks=True, enable_auto_update=True)
    notifier = FakeUpdateGateway(update=Update(latest_version="0.2.0"))

    with patch("vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["false"]):
        app = build_update_test_app(update_notifier=notifier, config=config)

        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            notifications = list(app._notifications)

    assert notifications
    notification = notifications[-1]
    assert notification.severity == "information"
    assert notification.title == "Update available"
    assert (
        notification.message
        == "0.1.0 => 0.2.0\nPlease update mistral-vibe with your package manager"
    )
