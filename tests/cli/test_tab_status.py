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

    def test_no_bell_when_focused(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_not_called()

    def test_action_required_blurred_shows_suffixed_title_and_bell(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()
        fake_app.reset_mock()

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_called_once()
        # Blurred: bell fires and title shows the action-required suffix.
        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Action Required\x07"
        )

    def test_action_required_focused_shows_indicator_no_bell(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        # Focused: no bell, but the waiting indicator renders.
        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_not_called()
        fake_app._driver.write.assert_called_once_with("\x1b]0;? Vibe\x07")

    def test_throttle_prevents_rapid_bells(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1

        adapter_enabled.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1

    def test_complete_fires_bell_and_suffixed_title(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()
        fake_app.reset_mock()

        adapter_enabled.notify(NotificationContext.COMPLETE)

        fake_app.bell.assert_called_once()
        # COMPLETE fires the bell and shows the task-complete suffix.
        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Task Complete\x07"
        )

    def test_focus_consumes_suffix_no_reappearance_on_blur(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()
        adapter_enabled.notify(NotificationContext.COMPLETE)
        fake_app.reset_mock()

        # User focuses: suffix is consumed.
        adapter_enabled.on_focus()
        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

        # User blurs again: suffix must not reappear.
        fake_app.reset_mock()
        adapter_enabled.on_blur()

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_clear_waiting_sets_plain_title(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.clear_waiting()

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_on_focus_renders_current_state(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()
        fake_app.reset_mock()
        adapter_enabled.on_focus()

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_on_focus_prevents_bell(
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

    def test_set_default_title_writes_when_focused(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.set_default_title("Fix login bug")

        fake_app._driver.write.assert_called_once_with("\x1b]0;Fix login bug\x07")

    def test_set_default_title_strips_control_characters(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.set_default_title("Fix\x1b]0;pwned\x07 login")

        (written,), _ = fake_app._driver.write.call_args
        assert written.startswith("\x1b]0;") and written.endswith("\x07")
        payload = written[4:-1]
        assert "\x1b" not in payload and "\x07" not in payload

    def test_set_default_title_writes_while_blurred_idle(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.on_blur()
        fake_app.reset_mock()

        adapter_enabled.set_default_title("Fix login bug")

        fake_app._driver.write.assert_called_once_with("\x1b]0;Fix login bug\x07")

    def test_set_default_title_noop_when_unchanged(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.set_default_title("Vibe")

        fake_app._driver.write.assert_not_called()

    def test_set_default_title_blank_falls_back_to_vibe(
        self, adapter_enabled: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        adapter_enabled.set_default_title("Custom")
        fake_app.reset_mock()

        adapter_enabled.set_default_title("   ")

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

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


class TestRunningIndicator:
    @pytest.fixture
    def running_adapter(self, fake_app: MagicMock) -> TextualNotificationAdapter:
        return TextualNotificationAdapter(
            fake_app,
            get_enabled=lambda: True,
            get_title_enabled=lambda: True,
            default_title="Vibe",
        )

    def test_running_shows_indicator_prefix(
        self, running_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        running_adapter.set_running(True)

        fake_app._driver.write.assert_called_once_with("\x1b]0;>> Vibe\x07")

    def test_running_persists_across_blur_focus(
        self, running_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        running_adapter.set_running(True)
        fake_app.reset_mock()

        running_adapter.on_blur()
        running_adapter.on_focus()

        fake_app._driver.write.assert_called_with("\x1b]0;>> Vibe\x07")

    def test_set_running_false_returns_to_idle(
        self, running_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        running_adapter.set_running(True)
        fake_app.reset_mock()

        running_adapter.set_running(False)

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_complete_after_running_when_blurred(
        self, running_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        running_adapter.on_blur()
        running_adapter.set_running(True)
        fake_app.reset_mock()

        # Turn ends: clear running, then fire the complete bell.
        running_adapter.set_running(False)
        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")
        fake_app.reset_mock()

        running_adapter.notify(NotificationContext.COMPLETE)

        fake_app.bell.assert_called_once()
        # COMPLETE shows the task-complete suffix when blurred.
        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Task Complete\x07"
        )

    def test_complete_clears_running_state_without_set_running_false(
        self, running_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        """notify(COMPLETE) is terminal: it clears RUNNING to IDLE even if
        the caller did not call set_running(False) first.
        """
        running_adapter.set_running(True)
        running_adapter.on_blur()
        fake_app.reset_mock()

        running_adapter.notify(NotificationContext.COMPLETE)

        fake_app.bell.assert_called_once()
        # Suffix shows while blurred.
        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Task Complete\x07"
        )
        # After focus consumes the suffix, title falls back to plain (IDLE),
        # not >> (RUNNING).
        fake_app.reset_mock()
        running_adapter.on_focus()
        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_set_default_title_updates_running_title_live(
        self, running_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        running_adapter.set_running(True)
        fake_app.reset_mock()

        running_adapter.set_default_title("Refactor auth")

        fake_app._driver.write.assert_called_once_with("\x1b]0;>> Refactor auth\x07")


class TestWaitingIndicator:
    @pytest.fixture
    def waiting_adapter(self, fake_app: MagicMock) -> TextualNotificationAdapter:
        return TextualNotificationAdapter(
            fake_app,
            get_enabled=lambda: True,
            get_title_enabled=lambda: True,
            default_title="Vibe",
        )

    def test_action_required_focused_shows_question_no_bell(
        self, waiting_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        waiting_adapter.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_not_called()
        fake_app._driver.write.assert_called_with("\x1b]0;? Vibe\x07")

    def test_focus_while_waiting_shows_question_no_bell(
        self, waiting_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        waiting_adapter.on_blur()
        waiting_adapter.notify(NotificationContext.ACTION_REQUIRED)
        fake_app.reset_mock()

        waiting_adapter.on_focus()

        fake_app.bell.assert_not_called()
        fake_app._driver.write.assert_called_with("\x1b]0;? Vibe\x07")

    def test_set_running_true_from_waiting_resumes(
        self, waiting_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        waiting_adapter.notify(NotificationContext.ACTION_REQUIRED)
        fake_app.reset_mock()

        waiting_adapter.set_running(True)

        fake_app._driver.write.assert_called_with("\x1b]0;>> Vibe\x07")

    def test_set_running_false_from_waiting_goes_idle(
        self, waiting_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        waiting_adapter.notify(NotificationContext.ACTION_REQUIRED)
        fake_app.reset_mock()

        waiting_adapter.set_running(False)

        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_set_default_title_updates_waiting_title_live(
        self, waiting_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        waiting_adapter.notify(NotificationContext.ACTION_REQUIRED)
        fake_app.reset_mock()

        # Auto-title refresh while waiting keeps the question indicator.
        waiting_adapter.set_default_title("Refactor auth")

        fake_app._driver.write.assert_called_with("\x1b]0;? Refactor auth\x07")

    def test_waiting_blurred_shows_suffixed_title(
        self, waiting_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        waiting_adapter.on_blur()
        fake_app.reset_mock()

        waiting_adapter.notify(NotificationContext.ACTION_REQUIRED)
        fake_app.reset_mock()

        # Re-render while blurred: title keeps the action-required suffix.
        waiting_adapter.set_default_title("New title")

        fake_app._driver.write.assert_called_with(
            "\x1b]0;New title - Action Required\x07"
        )


class TestTitleDisabled:
    """When experimental_enable_tab_status is off, the title still updates
    (plain title + notification suffix) but no >> / ? indicators are shown.
    """

    @pytest.fixture
    def title_disabled_adapter(self, fake_app: MagicMock) -> TextualNotificationAdapter:
        return TextualNotificationAdapter(
            fake_app,
            get_enabled=lambda: True,
            get_title_enabled=lambda: False,
            default_title="Vibe",
        )

    def test_no_indicator_when_disabled(
        self, title_disabled_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        title_disabled_adapter.set_running(True)

        # Title writes, but without the >> indicator.
        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_bell_fires_and_suffix_shows_when_notifications_on(
        self, title_disabled_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        title_disabled_adapter.on_blur()

        title_disabled_adapter.notify(NotificationContext.COMPLETE)

        fake_app.bell.assert_called_once()
        # No indicator, but the notification suffix still shows.
        fake_app._driver.write.assert_called_with("\x1b]0;Vibe - Task Complete\x07")

    def test_action_required_bell_fires_and_suffix_shows(
        self, title_disabled_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        title_disabled_adapter.on_blur()

        title_disabled_adapter.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_called_once()
        fake_app._driver.write.assert_called_with("\x1b]0;Vibe - Action Required\x07")


class TestTabStatusDisabledBellIso:
    """With experimental_enable_tab_status off, the bell must match the
    legacy adapter: fire immediately on notify when blurred, never defer
    to on_blur, and never fire when focused.
    """

    @pytest.fixture
    def bell_only_adapter(self, fake_app: MagicMock) -> TextualNotificationAdapter:
        return TextualNotificationAdapter(
            fake_app,
            get_enabled=lambda: True,
            get_title_enabled=lambda: False,
            default_title="Vibe",
        )

    def test_action_required_fires_immediately_when_blurred(
        self, bell_only_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        bell_only_adapter.on_blur()

        bell_only_adapter.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_called_once()

    def test_action_required_no_bell_when_focused(
        self, bell_only_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        bell_only_adapter.notify(NotificationContext.ACTION_REQUIRED)

        fake_app.bell.assert_not_called()

    def test_complete_fires_immediately_when_blurred(
        self, bell_only_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        bell_only_adapter.on_blur()

        bell_only_adapter.notify(NotificationContext.COMPLETE)

        fake_app.bell.assert_called_once()

    def test_on_blur_does_not_fire_bell(
        self, bell_only_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        # notify while focused: no bell. Then blur: still no bell (legacy
        # behavior — the bell is not deferred to on_blur when tab status is off).
        bell_only_adapter.notify(NotificationContext.ACTION_REQUIRED)
        fake_app.reset_mock()

        bell_only_adapter.on_blur()

        fake_app.bell.assert_not_called()
        # No bell fired while focused, so no suffix should appear on blur.
        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_throttle_prevents_rapid_bells(
        self, bell_only_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        bell_only_adapter.on_blur()

        bell_only_adapter.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1

        bell_only_adapter.notify(NotificationContext.ACTION_REQUIRED)
        assert fake_app.bell.call_count == 1

    def test_no_indicators_when_tab_status_off(
        self, bell_only_adapter: TextualNotificationAdapter, fake_app: MagicMock
    ) -> None:
        bell_only_adapter.on_blur()
        bell_only_adapter.notify(NotificationContext.ACTION_REQUIRED)
        bell_only_adapter.set_running(True)
        bell_only_adapter.set_default_title("New title")

        # Title always writes, but never with >> or ? indicators.
        for call in fake_app._driver.write.call_args_list:
            (written,), _ = call
            assert ">>" not in written
            assert "?" not in written


class TestFlagCombinations:
    """Exhaustive matrix of the two flags (notifications, tab-status) and the
    expected title/bell behavior for each combination.
    """

    def _adapter(
        self, fake_app: MagicMock, *, notifications: bool, tab_status: bool
    ) -> TextualNotificationAdapter:
        return TextualNotificationAdapter(
            fake_app,
            get_enabled=lambda: notifications,
            get_title_enabled=lambda: tab_status,
            default_title="Vibe",
        )

    def test_both_off_title_updates_no_bell_no_suffix_no_indicators(
        self, fake_app: MagicMock
    ) -> None:
        adapter = self._adapter(fake_app, notifications=False, tab_status=False)
        adapter.set_default_title("My session")

        fake_app._driver.write.assert_called_once_with("\x1b]0;My session\x07")
        fake_app.bell.assert_not_called()

    def test_both_off_notify_does_nothing(self, fake_app: MagicMock) -> None:
        adapter = self._adapter(fake_app, notifications=False, tab_status=False)
        adapter.on_blur()
        fake_app.reset_mock()

        adapter.notify(NotificationContext.COMPLETE)

        # No bell. State transitions to IDLE and renders, but with both flags
        # off the title is plain — no indicator, no suffix.
        fake_app.bell.assert_not_called()
        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_notifications_on_tab_status_off_suffix_shows_no_indicators(
        self, fake_app: MagicMock
    ) -> None:
        adapter = self._adapter(fake_app, notifications=True, tab_status=False)
        adapter.on_blur()
        fake_app.reset_mock()

        adapter.notify(NotificationContext.COMPLETE)

        fake_app.bell.assert_called_once()
        fake_app._driver.write.assert_called_once_with(
            "\x1b]0;Vibe - Task Complete\x07"
        )

    def test_notifications_off_tab_status_on_indicators_no_bell_no_suffix(
        self, fake_app: MagicMock
    ) -> None:
        adapter = self._adapter(fake_app, notifications=False, tab_status=True)
        adapter.set_running(True)

        # Indicator shows (tab-status on), no bell, no suffix.
        fake_app._driver.write.assert_called_once_with("\x1b]0;>> Vibe\x07")

        adapter.on_blur()
        fake_app.reset_mock()
        adapter.notify(NotificationContext.COMPLETE)

        # Notifications off: no bell, no suffix. But the state transition to
        # IDLE still renders — the indicator clears because tab-status is on.
        fake_app.bell.assert_not_called()
        fake_app._driver.write.assert_called_once_with("\x1b]0;Vibe\x07")

    def test_notifications_off_tab_status_on_action_required_shows_waiting(
        self, fake_app: MagicMock
    ) -> None:
        adapter = self._adapter(fake_app, notifications=False, tab_status=True)

        adapter.notify(NotificationContext.ACTION_REQUIRED)

        # No bell, no suffix, but the waiting indicator renders.
        fake_app.bell.assert_not_called()
        fake_app._driver.write.assert_called_once_with("\x1b]0;? Vibe\x07")

    def test_both_on_suffix_prio_over_indicator_when_blurred(
        self, fake_app: MagicMock
    ) -> None:
        adapter = self._adapter(fake_app, notifications=True, tab_status=True)
        adapter.set_running(True)
        adapter.on_blur()
        fake_app.reset_mock()

        # Turn ends: caller clears running first, then fires COMPLETE.
        adapter.set_running(False)
        fake_app.reset_mock()
        adapter.notify(NotificationContext.COMPLETE)

        fake_app.bell.assert_called_once()
        fake_app._driver.write.assert_called_with("\x1b]0;Vibe - Task Complete\x07")

    def test_both_on_indicator_shows_when_focused(self, fake_app: MagicMock) -> None:
        adapter = self._adapter(fake_app, notifications=True, tab_status=True)
        adapter.set_running(True)

        # Focused: indicator shows, no suffix (focused user sees the prompt).
        fake_app._driver.write.assert_called_once_with("\x1b]0;>> Vibe\x07")
