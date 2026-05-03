from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.quit_manager import QUIT_CONFIRM_DELAY, QuitManager


@pytest.fixture
def app() -> VibeApp:
    return build_test_vibe_app()


@pytest.fixture
def qm() -> QuitManager:
    mock_app = MagicMock()
    mock_app.query_one.side_effect = Exception("not mounted")
    mock_app.set_timer.return_value = MagicMock()
    return QuitManager(mock_app)


class TestQuitManager:
    def test_not_confirmed_initially(self, qm: QuitManager) -> None:
        assert qm.is_confirmed("Ctrl+C") is False
        assert qm.is_confirmed("Ctrl+D") is False

    def test_confirmed_within_delay(self, qm: QuitManager) -> None:
        qm.request_confirmation("Ctrl+C")
        assert qm.is_confirmed("Ctrl+C") is True

    def test_wrong_key_not_confirmed(self, qm: QuitManager) -> None:
        qm.request_confirmation("Ctrl+C")
        assert qm.is_confirmed("Ctrl+D") is False

    def test_expired_not_confirmed(self, qm: QuitManager) -> None:
        qm.request_confirmation("Ctrl+C")
        qm._confirm_time = time.monotonic() - QUIT_CONFIRM_DELAY - 0.1
        assert qm.is_confirmed("Ctrl+C") is False

    def test_request_resets_timer_on_key_switch(self, qm: QuitManager) -> None:
        qm.request_confirmation("Ctrl+C")
        qm._confirm_time = time.monotonic() - QUIT_CONFIRM_DELAY + 0.05
        qm.request_confirmation("Ctrl+D")
        assert qm.is_confirmed("Ctrl+D") is True

    def test_confirm_key_property(self, qm: QuitManager) -> None:
        assert qm.confirm_key is None
        qm.request_confirmation("Ctrl+D")
        assert qm.confirm_key == "Ctrl+D"

    def test_request_schedules_cancel_timer(self, qm: QuitManager) -> None:
        qm.request_confirmation("Ctrl+D")
        mock_app = qm._app
        assert isinstance(mock_app, MagicMock)
        mock_app.set_timer.assert_called_once_with(
            QUIT_CONFIRM_DELAY, qm.cancel_confirmation
        )

    def test_request_stops_previous_timer(self, qm: QuitManager) -> None:
        qm.request_confirmation("Ctrl+C")
        first_timer = qm._confirm_timer
        assert isinstance(first_timer, MagicMock)
        qm.request_confirmation("Ctrl+D")
        first_timer.stop.assert_called_once()

    def test_cancel_confirmation_resets_state(self, qm: QuitManager) -> None:
        qm.request_confirmation("Ctrl+C")
        qm.cancel_confirmation()
        assert qm.is_confirmed("Ctrl+C") is False
        assert qm.confirm_key is None
        assert qm._confirm_timer is None

    def test_cancel_confirmation_noop_when_idle(self, qm: QuitManager) -> None:
        qm.cancel_confirmation()
        assert qm.confirm_key is None


class TestActionInterruptOrQuit:
    def test_clears_input_when_has_value(self, app: VibeApp) -> None:
        mock_container = MagicMock()
        mock_container.value = "some text"
        with patch.object(app, "_get_chat_input", return_value=mock_container):
            app.action_interrupt_or_quit()
        assert mock_container.value == ""

    def test_skips_empty_input(self, app: VibeApp) -> None:
        mock_container = MagicMock()
        mock_container.value = ""
        with (
            patch.object(app, "_get_chat_input", return_value=mock_container),
            patch.object(app, "_try_interrupt", return_value=False),
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_interrupt_or_quit()
        mock_confirm.assert_called_once_with("Ctrl+C")

    def test_quits_on_confirmed(self, app: VibeApp) -> None:
        app._quit_manager._confirm_time = time.monotonic()
        app._quit_manager._confirm_key = "Ctrl+C"
        with (
            patch.object(app, "_get_chat_input", return_value=None),
            patch.object(app, "_force_quit") as mock_quit,
        ):
            app.action_interrupt_or_quit()
        mock_quit.assert_called_once()

    def test_interrupts_before_requesting_confirmation(self, app: VibeApp) -> None:
        with (
            patch.object(app, "_get_chat_input", return_value=None),
            patch.object(app, "_try_interrupt", return_value=True) as mock_interrupt,
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_interrupt_or_quit()
        mock_interrupt.assert_called_once()
        mock_confirm.assert_not_called()

    def test_requests_confirmation_when_nothing_to_interrupt(
        self, app: VibeApp
    ) -> None:
        with (
            patch.object(app, "_get_chat_input", return_value=None),
            patch.object(app, "_try_interrupt", return_value=False),
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_interrupt_or_quit()
        mock_confirm.assert_called_once_with("Ctrl+C")


class TestActionDeleteRightOrQuit:
    def test_deletes_right_when_input_has_value(self, app: VibeApp) -> None:
        mock_input = MagicMock()
        mock_container = MagicMock()
        mock_container.value = "some text"
        mock_container.input_widget = mock_input
        with patch.object(app, "_get_chat_input", return_value=mock_container):
            app.action_delete_right_or_quit()
        mock_input.action_delete_right.assert_called_once()

    def test_skips_empty_input(self, app: VibeApp) -> None:
        mock_container = MagicMock()
        mock_container.value = ""
        with (
            patch.object(app, "_get_chat_input", return_value=mock_container),
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_delete_right_or_quit()
        mock_confirm.assert_called_once_with("Ctrl+D")

    def test_quits_on_confirmed(self, app: VibeApp) -> None:
        app._quit_manager._confirm_time = time.monotonic()
        app._quit_manager._confirm_key = "Ctrl+D"
        with (
            patch.object(app, "_get_chat_input", return_value=None),
            patch.object(app, "_force_quit") as mock_quit,
        ):
            app.action_delete_right_or_quit()
        mock_quit.assert_called_once()

    def test_requests_confirmation_when_no_input(self, app: VibeApp) -> None:
        with (
            patch.object(app, "_get_chat_input", return_value=None),
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_delete_right_or_quit()
        mock_confirm.assert_called_once_with("Ctrl+D")
