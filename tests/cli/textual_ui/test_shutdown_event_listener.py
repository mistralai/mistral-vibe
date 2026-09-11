from __future__ import annotations

import asyncio

import pytest
from textual.worker import Worker, WorkerState

from tests.conftest import build_test_vibe_app
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)


async def _wait_for_event_worker(app, pilot) -> Worker[None]:
    for _ in range(40):
        if app._app_server_events_worker is not None:
            return app._app_server_events_worker
        await pilot.pause(0.05)
    raise AssertionError("app-server event worker never started")


async def _wait_for_worker_to_finish(worker: Worker[None], pilot) -> None:
    for _ in range(40):
        if worker.state in (WorkerState.SUCCESS, WorkerState.ERROR):
            return
        await pilot.pause(0.05)
    raise AssertionError(f"event worker never finished (state={worker.state})")


@pytest.mark.asyncio
async def test_event_listener_finishes_cleanly_when_connection_drops_during_shutdown() -> (
    None
):
    """During shutdown a dropped connection can trigger a failed reconnect/resume
    that closes the event stream with an error. Because the session is closing,
    the listener must finish cleanly instead of propagating that error and
    crashing the Textual worker (which dumps a traceback on the way out).
    """
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        worker = await _wait_for_event_worker(app, pilot)

        # Shutdown has begun (as _stop_app_server_event_listener signals).
        app.app_server.begin_close()
        # A connection drop then a rejected session/resume closes the stream.
        app.app_server._close_event_streams(
            AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS,
                    message="Invalid request parameters",
                )
            )
        )

        await _wait_for_worker_to_finish(worker, pilot)

        assert worker.state is WorkerState.SUCCESS
        assert app.is_running


@pytest.mark.asyncio
async def test_stop_event_listener_cancels_worker_while_dom_is_intact() -> None:
    """The event listener is cancelled before the DOM is torn down.

    A queued turn (e.g. a skill) streams effects through
    ``_listen_app_server_events``, which mounts widgets. Textual only cancels
    workers *after* ``_shutdown`` has closed the screen, so unless the listener
    is stopped first the worker mounts into a closing tree and raises
    ``MountError``. ``_stop_app_server_event_listener`` must stop it while the
    messages area is still attached.
    """
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        worker = await _wait_for_event_worker(app, pilot)
        messages_area = app.query_one("#messages")
        assert messages_area.is_attached

        await app._stop_app_server_event_listener()

        assert worker.state is WorkerState.CANCELLED
        assert app._app_server_events_worker is None
        # The worker is stopped without the DOM having been torn down.
        assert messages_area.is_attached


@pytest.mark.asyncio
async def test_stop_event_listener_waits_for_in_flight_handler() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        worker = await _wait_for_event_worker(app, pilot)
        await app._app_server_event_handler_lock.acquire()
        stop_task = asyncio.create_task(app._stop_app_server_event_listener())
        try:
            await pilot.pause()
            assert not stop_task.done()
        finally:
            app._app_server_event_handler_lock.release()

        await stop_task
        assert worker.state is WorkerState.CANCELLED


@pytest.mark.asyncio
async def test_shutdown_stops_the_event_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_shutdown`` must stop the listener before delegating to Textual."""
    app = build_test_vibe_app()
    stopped = asyncio.Event()
    async with app.run_test() as pilot:
        await _wait_for_event_worker(app, pilot)
        original = app._stop_app_server_event_listener

        async def _tracked() -> None:
            stopped.set()
            await original()

        monkeypatch.setattr(app, "_stop_app_server_event_listener", _tracked)

    # Exiting run_test drives _shutdown, which must invoke our stop step.
    assert stopped.is_set()
