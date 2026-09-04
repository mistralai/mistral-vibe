from __future__ import annotations

import asyncio

import pytest
from textual.worker import Worker, WorkerState

from tests.conftest import build_test_vibe_app


async def _wait_for_event_worker(app, pilot) -> Worker[None]:
    for _ in range(40):
        if app._app_server_events_worker is not None:
            return app._app_server_events_worker
        await pilot.pause(0.05)
    raise AssertionError("app-server event worker never started")


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
