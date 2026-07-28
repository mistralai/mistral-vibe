from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress

from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.client import AppServerClient

_EVENT_QUEUE_MAX_SIZE = 64


class BoundedEventQueue[EventT](asyncio.Queue[EventT]):
    def __init__(self) -> None:
        super().__init__(maxsize=_EVENT_QUEUE_MAX_SIZE)


def finish_event_queue[EventT](
    events: asyncio.Queue[EventT], terminal_event: EventT
) -> None:
    while not events.empty():
        events.get_nowait()
    events.put_nowait(terminal_event)


async def stream_until_complete[EventT, ResultT](
    events: asyncio.Queue[EventT],
    completion: asyncio.Task[ResultT],
    *,
    event_task_name: str | None = None,
) -> AsyncGenerator[EventT, None]:
    pending_event: asyncio.Task[EventT] | None = None
    try:
        while not completion.done():
            pending_event = asyncio.create_task(events.get(), name=event_task_name)
            done, _ = await asyncio.wait(
                (completion, pending_event), return_when=asyncio.FIRST_COMPLETED
            )
            if pending_event in done:
                yield pending_event.result()
                pending_event = None
                continue
            pending_event.cancel()
            with suppress(asyncio.CancelledError):
                await pending_event
            pending_event = None

        while not events.empty():
            yield events.get_nowait()
    finally:
        if pending_event is not None and not pending_event.done():
            pending_event.cancel()
            with suppress(asyncio.CancelledError):
                await pending_event


async def stream_request[EventT, ResponseT: ProtocolModel](
    client: AppServerClient,
    method: str,
    params: ProtocolModel,
    events: asyncio.Queue[EventT],
    response_type: type[ResponseT],
) -> AsyncGenerator[EventT | ResponseT, None]:
    request = asyncio.create_task(
        client.request(method, params, wait_for_incoming=True)
    )
    try:
        async for event in stream_until_complete(events, request):
            yield event
        yield validate_wire(response_type, await request)
    finally:
        if not request.done():
            request.cancel()
            with suppress(asyncio.CancelledError):
                await request
