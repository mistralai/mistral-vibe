from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing, suppress

from vibe.app_server._dispatch import DispatchResult
from vibe.app_server._session_backend_port import SessionBackendHostLeaseWatch
from vibe.app_server.protocol import (
    ProtocolModel,
    SessionLeaseEnded,
    SessionLeaseHolder,
    SessionLeaseSubscribeResponse,
    SessionLeaseUpdate,
)
from vibe.observability.logging import logger

type SendNotification = Callable[[str, ProtocolModel], Awaitable[None]]
type LeaseChanges = AsyncGenerator[dict[str, SessionLeaseHolder | None], None]


class SessionLeaseSubscription:
    def __init__(self, send_notification: SendNotification) -> None:
        self._send_notification = send_notification
        self._task: asyncio.Task[None] | None = None
        self._subscribing = asyncio.Lock()

    async def subscribe(self, host: SessionBackendHostLeaseWatch) -> DispatchResult:
        async with self._subscribing:
            for task in self.take():
                task.cancel()
            leases = host.watch_leases()
            try:
                held = await anext(leases)
            except BaseException:
                await leases.aclose()
                raise
            written: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(self._publish(leases, written))
            self._task = task
            task.add_done_callback(self._finished)
        return DispatchResult(
            SessionLeaseSubscribeResponse(
                held=[
                    SessionLeaseUpdate(session_id=session_id, lease_holder=holder)
                    for session_id, holder in sorted(held.items())
                ]
            ),
            after_response=lambda: _settle(written, True),
            on_response_abandoned=lambda: _settle(written, False),
        )

    def take(self) -> list[asyncio.Task[None]]:
        task = self._task
        self._task = None
        return [] if task is None else [task]

    async def _publish(
        self, leases: LeaseChanges, written: asyncio.Future[bool]
    ) -> None:
        async with aclosing(leases):
            if not await written:
                return
            try:
                async for changes in leases:
                    for session_id, holder in changes.items():
                        await self._send_notification(
                            "session/lease/updated",
                            SessionLeaseUpdate(
                                session_id=session_id, lease_holder=holder
                            ),
                        )
            except Exception:
                logger.warning("Session lease subscription stopped", exc_info=True)
                with suppress(Exception):
                    await self._send_notification(
                        "session/lease/ended", SessionLeaseEnded()
                    )

    def _finished(self, task: asyncio.Task[None]) -> None:
        if self._task is task:
            self._task = None


def _settle(future: asyncio.Future[bool], written: bool) -> None:
    if not future.done():
        future.set_result(written)
