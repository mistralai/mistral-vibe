from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from vibe.cli.textual_ui.notifications import (
    NotificationContext,
    TextualNotificationAdapter,
)


def _make_fake_app(*, is_headless: bool = False) -> MagicMock:
    app = MagicMock()
    type(app).is_headless = PropertyMock(return_value=is_headless)
    return app


@pytest.fixture
def fake_app() -> MagicMock:
    return _make_fake_app()


@pytest.fixture
def adapter_enabled(fake_app: MagicMock) -> TextualNotificationAdapter:
    return TextualNotificationAdapter(
        fake_app, get_enabled=lambda: True, default_title="Vibe"
    )


@pytest.fixture
def adapter_disabled(fake_app: MagicMock) -> TextualNotificationAdapter:
    return TextualNotificationAdapter(
        fake_app, get_enabled=lambda: False, default_title="Vibe"
    )


class TestTextualNotificationAdapter:
    def test_no_notification_when_disabled(
        self, adapter_disabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_disabled.on_blur()

        adapter_disabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_not_called()

    def test_no_notification_when_focused(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_not_called()

    def test_notification_sent_when_unfocused_and_enabled(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_called_once()
        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Action Required\x07"
        )

    def test_throttle_prevents_rapid_notifications(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1

    def test_contextual_title_for_action_required(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Action Required\x07"
        )

    def test_contextual_title_for_complete(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()

        adapter_enabled.notify(NotificationContext.COMPLETE)

        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Task Complete\x07"
        )

    def test_restore_sets_default_title(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.restore()

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_on_focus_restores_title(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()
        adapter_enabled.on_focus()

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_on_focus_prevents_notifications(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()
        adapter_enabled.on_focus()
        fake_app.reset_mock()

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_not_called()

    def test_no_title_write_when_headless(self) -> None:
        app = _make_fake_app(is_headless=True)
        adapter = TextualNotificationAdapter(
            app, get_enabled=lambda: True, default_title="Vibe"
        )
        adapter.on_blur()

        adapter.notify(NotificationContext.ACTION_REQUIRED)

        app.bell.assert_called_once()
        app._driver.write.assert_not_called()

    def test_enabled_callback_reads_live_value(self, fake_app: MagicMock) -> None:
        enabled = True
        adapter = TextualNotificationAdapter(
            fake_app, get_enabled=lambda: enabled, default_title="Vibe"
        )
        adapter.on_blur()

        adapter.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1

        enabled = False
        adapter._last_notification_time = 0.0
        adapter.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1
