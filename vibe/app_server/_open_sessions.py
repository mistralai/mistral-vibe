"""The sessions every channel of this process has open.

A multiplexed app server opens each session on its own channel, with its own
session Host. Archiving the sessions a session started reaches sessions other
channels hold: archived from the store, the channel holding one would save its
own metadata over the archive. So each Host registers here, and the archive
goes through the Host that holds the session. A setting written without a
session reaches the open ones the same way: each re-reads its config files.
A session whose channel closed mid-turn is parked here, still running, until
a channel resumes it or its turn work ends.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
import logging
from typing import Protocol

from vibe.app_server.protocol import SessionArchiveParams, SessionArchiveResponse


class OpenSessionHost(Protocol):
    async def archive_open(
        self, params: SessionArchiveParams
    ) -> SessionArchiveResponse | None:
        """None when this Host does not hold the session open."""
        ...

    async def reload_config_files(self) -> None:
        """Have every open session re-read the config files."""
        ...

    def open_selection(self, session_id: str) -> tuple[str, str] | None:
        """The agent and policy the session runs now; None when this Host does
        not hold it open.
        """
        ...


logger = logging.getLogger(__name__)

_PARK_POLL_S = 0.5
# How long the exit waits for parked-session stops already under way.
_STOPPING_GRACE_S = 30.0


class ParkedSession(Protocol):
    def has_turn_work(self) -> bool: ...

    async def stop(self) -> None: ...


class OpenSessions:
    def __init__(self) -> None:
        self._hosts: list[OpenSessionHost] = []
        self._parked: dict[str, ParkedSession] = {}
        self._watchers: set[asyncio.Task[None]] = set()
        self._stopping: set[asyncio.Task[None]] = set()
        self._settling: dict[str, asyncio.Future[None]] = {}
        self._resumes: dict[str, asyncio.Future[None]] = {}

    def register(self, host: OpenSessionHost) -> None:
        if host not in self._hosts:
            self._hosts.append(host)

    def unregister(self, host: OpenSessionHost) -> None:
        if host in self._hosts:
            self._hosts.remove(host)

    async def archive(
        self, params: SessionArchiveParams
    ) -> SessionArchiveResponse | None:
        """Archived through the Host that holds the session open; None when
        none does.
        """
        for host in tuple(self._hosts):
            response = await host.archive_open(params)
            if response is not None:
                return response
        return None

    async def reload_config_files(self) -> None:
        """Every open session re-reads the config files, so a setting written
        without a session takes effect in the sessions already open.
        """
        for host in tuple(self._hosts):
            await host.reload_config_files()

    def selection(self, session_id: str) -> tuple[str, str] | None:
        """The agent and policy an open session runs now, whichever channel
        holds it.
        """
        for host in tuple(self._hosts):
            selection = host.open_selection(session_id)
            if selection is not None:
                return selection
        return None

    def park(self, session_id: str, parked: ParkedSession) -> None:
        """Keep the session running with no channel, until a channel takes it
        or its turn work ends.
        """
        self.attached(session_id)
        self._parked[session_id] = parked
        watcher = asyncio.create_task(
            self._stop_when_idle(session_id, parked), name="vibe-parked-session"
        )
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)

    def take_parked(self, session_id: str) -> ParkedSession | None:
        """Take the session for an attach: a resume meanwhile waits until
        ``attached`` or ``park`` settles it.
        """
        parked = self._parked.pop(session_id, None)
        if parked is not None:
            self._busy(session_id)
        return parked

    def attached(self, session_id: str) -> None:
        settled = self._settling.pop(session_id, None)
        if settled is not None and not settled.done():
            settled.set_result(None)

    def parked(self, session_id: str) -> ParkedSession | None:
        return self._parked.get(session_id)

    def held_open(self, session_id: str) -> bool:
        return self.selection(session_id) is not None

    async def begin_resume(self, session_id: str) -> asyncio.Future[None]:
        """Claim the session for the process's own resume, once whatever
        settles it is done: a client resume waits until ``end_resume``.
        """
        while (claim := self._resumes.get(session_id)) is not None or (
            session_id in self._settling
        ):
            if claim is not None:
                await asyncio.shield(claim)
            else:
                await self.wait_stopped(session_id)
        claim = asyncio.get_running_loop().create_future()
        self._resumes[session_id] = claim
        return claim

    def end_resume(self, session_id: str, claim: asyncio.Future[None]) -> None:
        if self._resumes.get(session_id) is claim:
            del self._resumes[session_id]
        if not claim.done():
            claim.set_result(None)

    @contextlib.asynccontextmanager
    async def opening(self, session_id: str) -> AsyncIterator[None]:
        """A client opens the session from the store once whatever settles it
        is done: the process's own resume of it waits, then finds it held.
        """
        while session_id in self._settling:
            await self.wait_stopped(session_id)
        mine = asyncio.get_running_loop().create_future()
        self._settling[session_id] = mine
        try:
            yield
        finally:
            if self._settling.get(session_id) is mine:
                del self._settling[session_id]
            if not mine.done():
                mine.set_result(None)

    def resuming(self, session_id: str) -> bool:
        return session_id in self._resumes

    async def wait_resumed(self, session_id: str) -> None:
        claim = self._resumes.get(session_id)
        if claim is not None:
            await asyncio.shield(claim)

    def settling(self, session_id: str) -> bool:
        return session_id in self._settling

    async def wait_stopped(self, session_id: str) -> None:
        """Until a parked session being stopped or attached is settled."""
        settling = self._settling.get(session_id)
        if settling is not None:
            await asyncio.shield(settling)

    def _busy(self, session_id: str) -> None:
        if session_id not in self._settling:
            self._settling[session_id] = asyncio.get_running_loop().create_future()

    async def _stop_when_idle(self, session_id: str, parked: ParkedSession) -> None:
        while parked.has_turn_work():
            await asyncio.sleep(_PARK_POLL_S)
            if self._parked.get(session_id) is not parked:
                return
        if self._parked.get(session_id) is not parked:
            return
        del self._parked[session_id]
        self._busy(session_id)
        # The exit lets a stop under way finish rather than cancel it midway.
        task = asyncio.current_task()
        if task is not None:
            self._watchers.discard(task)
            self._stopping.add(task)
            task.add_done_callback(self._stopping.discard)
        if await _stop(session_id, parked):
            self.attached(session_id)
        else:
            # Kept, so the process exit tries the stop again.
            self._parked[session_id] = parked
            self.attached(session_id)

    async def close(self) -> None:
        for watcher in tuple(self._watchers):
            watcher.cancel()
        await asyncio.gather(*self._watchers, return_exceptions=True)
        if self._stopping:
            _done, late = await asyncio.wait(
                tuple(self._stopping), timeout=_STOPPING_GRACE_S
            )
            if late:
                logger.warning("%d parked-session stops outlived the exit", len(late))
                for task in late:
                    task.cancel()
                await asyncio.gather(*late, return_exceptions=True)
        parked = self._parked
        self._parked = {}
        await asyncio.gather(
            *(_stop(session_id, session) for session_id, session in parked.items())
        )


async def _stop(session_id: str, parked: ParkedSession) -> bool:
    try:
        await parked.stop()
    except Exception:
        logger.warning("Could not stop parked session %s", session_id, exc_info=True)
        return False
    return True


__all__ = ["OpenSessionHost", "OpenSessions", "ParkedSession"]
