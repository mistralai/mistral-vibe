from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.app import VibeApp, _run_app_with_cleanup
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
            patch.object(app, "_try_interrupt_no_job_steps", return_value=False),
            patch.object(app, "_try_interrupt_running_job", return_value=False),
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_interrupt_or_quit()
        mock_confirm.assert_called_once_with("Ctrl+C", "")

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
            patch.object(
                app, "_try_interrupt_no_job_steps", return_value=True
            ) as mock_interrupt,
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
            patch.object(app, "_try_interrupt_no_job_steps", return_value=False),
            patch.object(app, "_try_interrupt_running_job", return_value=False),
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_interrupt_or_quit()
        mock_confirm.assert_called_once_with("Ctrl+C", "")


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
        mock_confirm.assert_called_once_with("Ctrl+D", "")

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
        mock_confirm.assert_called_once_with("Ctrl+D", "")

    def test_shows_queue_warning_when_queue_non_empty(self, app: VibeApp) -> None:
        app._input_queue.append_prompt("queued")
        with (
            patch.object(app, "_get_chat_input", return_value=None),
            patch.object(app._quit_manager, "request_confirmation") as mock_confirm,
        ):
            app.action_delete_right_or_quit()
        mock_confirm.assert_called_once_with(
            "Ctrl+D", "1 queued message will be discarded"
        )


@pytest.mark.asyncio
async def test_shutdown_cleanup_cancels_in_flight_tasks(app: VibeApp) -> None:
    async def _pending() -> None:
        await asyncio.Event().wait()

    agent_task = asyncio.create_task(_pending())
    bash_task = asyncio.create_task(_pending())
    app._agent_task = agent_task
    app._bash_task = bash_task

    await asyncio.wait_for(app.shutdown_cleanup(), timeout=1.0)

    assert agent_task.cancelled()
    assert bash_task.cancelled()


@pytest.mark.asyncio
async def test_shutdown_disables_future_queue_drains(app: VibeApp) -> None:
    app._input_queue.append_prompt("queued")

    await app._begin_shutdown()

    with patch("vibe.cli.textual_ui.message_queue.asyncio.create_task") as create_task:
        app._queue.start_drain_if_needed()

    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_begin_shutdown_stops_scheduled_loop_runner(app: VibeApp) -> None:
    with (
        patch.object(app._queue, "shutdown", new_callable=AsyncMock) as queue_shutdown,
        patch.object(app._loop_runner, "stop", new_callable=AsyncMock) as loop_stop,
    ):
        await app._begin_shutdown()

    queue_shutdown.assert_awaited_once()
    loop_stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_cleanup_flushes_telemetry(app: VibeApp) -> None:
    with patch.object(
        app.agent_loop.telemetry_client, "aclose", new_callable=AsyncMock
    ) as telemetry_aclose:
        await app.shutdown_cleanup()

    telemetry_aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_app_with_cleanup_runs_cleanup_when_run_async_raises(
    app: VibeApp,
) -> None:
    with (
        patch.object(
            app, "run_async", new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ),
        patch.object(app, "shutdown_cleanup", new_callable=AsyncMock) as cleanup,
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await _run_app_with_cleanup(app)

    cleanup.assert_awaited_once()
