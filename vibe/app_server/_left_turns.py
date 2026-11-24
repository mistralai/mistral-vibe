"""Turns an earlier process left mid-flight, carried on by the server itself.

A client opts in at ``initialize`` (``capabilities.resumeLeftTurns``). Once,
per owner, the process lists the sessions that owner left mid-turn and resumes
each on a channel of its own that no client holds: the channel closes as soon
as the resume is answered, so the running session is parked (see
``_parked_sessions``) and carries on, borrowing its account's sign-in from a
live channel. A client that opens the session later attaches to it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Literal, Protocol

from vibe.app_server._connection_protocol import (
    ClientAccount,
    ClientInfo,
    InitializeParams,
)
from vibe.app_server._platform_credential_lenders import (
    AccountKey,
    LenderAvailability,
    PlatformCredentialLenders,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolErrorCode,
    ServerRequest,
    SessionListParams,
    SessionListResponse,
    SessionResumeParams,
)
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair

if TYPE_CHECKING:
    from vibe.app_server._open_sessions import OpenSessions

logger = logging.getLogger(__name__)

# Resumes started at once: each opens a session, replays its store, and sends
# a model call.
_CONCURRENCY = 4
# A resume that takes longer, its wait for the session included, is given up:
# its channel closes, which parks the session if its turn runs (it then reads
# as running), else it reads as failed. A client resume of the session waits
# for it as long at most.
RESUME_TIMEOUT_S = 120.0
# After a failed sweep of the session leases, as a lease watch retries.
_WATCH_RETRY_S = 2.0
# How often a process without the owner's claim tries to take it.
_CLAIM_RETRY_S = 2.0

type Owner = tuple[str, str, str, str]
# Resumes one session on a channel of its own. With ``interrupt``, its left
# turn ends as interrupted instead, and nothing runs.
type ResumeOne = Callable[[str, bool], Awaitable[None]]
type ListLeft = Callable[[], Awaitable[list[str]]]


class LenderOf(Protocol):
    def availability(self) -> LenderAvailability: ...

    def changed(self) -> asyncio.Future[None]: ...


class SignsInItself:
    def availability(self) -> LenderAvailability:
        return "durable"

    def changed(self) -> asyncio.Future[None]:
        return asyncio.get_running_loop().create_future()


@dataclass(frozen=True, slots=True)
class AccountLender:
    """Whether the process has a lender for ``account`` that outlives a chat."""

    lenders: PlatformCredentialLenders
    account: AccountKey

    def availability(self) -> LenderAvailability:
        return self.lenders.availability(self.account)

    def changed(self) -> asyncio.Future[None]:
        return self.lenders.changed(self.account)


# Opens a channel no client holds, initialized as ``InitializeParams`` says,
# resumes the session there (``interruptLeftTurn`` as asked), and closes the
# channel once that is answered.
type OpenLeftTurnChannel = Callable[[InitializeParams, str, bool], Awaitable[None]]
# Opens a channel no client holds, initialized as ``InitializeParams`` says,
# and lists there the sessions its owner left mid-turn.
type ListOnOwnChannel = Callable[[InitializeParams], Awaitable[list[str]]]

# ``not_resuming``: nothing resumes this owner's left turns in this process;
# ``resumed_elsewhere``: another process holds the owner's claim and resumes
# them.
LeftTurnsStatus = Literal[
    "not_resuming",
    "listing",
    "listed",
    "listing_failed",
    "listing_timed_out",
    "resumed_elsewhere",
]
# ``waiting_for_lender``: no process-lifetime channel lends its account a
# sign-in yet, so it would run without one; ``running_elsewhere``: another
# process holds the session open.
LeftTurnState = Literal[
    "waiting_for_lender", "queued", "resuming", "running", "failed", "running_elsewhere"
]
LeftTurnOutcome = Literal[
    "interrupted",
    "ended_before_running",
    "still_running",
    "taken_by_client",
    "already_ended",
    "failed",
    "running_elsewhere",
]


type ResumeClaim = asyncio.Future[None]
# ``taken``: a client holds the session open, so the process left it alone;
# ``adopted``: it already runs with no client, so the process carries it on;
# ``ended``: its turn ended before the process got to it; ``busy``: another
# process holds it open.
type _Resumed = Literal["resumed", "adopted", "failed", "taken", "ended", "busy"]


class ProcessSessions(Protocol):
    def parked(self, session_id: str) -> object | None: ...

    def held_open(self, session_id: str) -> bool: ...

    def resuming(self, session_id: str) -> bool: ...

    async def begin_resume(self, session_id: str) -> ResumeClaim: ...

    def end_resume(self, session_id: str, claim: ResumeClaim) -> None: ...


class OwnerClaim(Protocol):
    """Which process resumes an owner's left turns, among those sharing the
    store (see ``_left_turns_claim``).
    """

    async def acquire(self) -> bool: ...

    async def publish(self, session_ids: list[str]) -> None: ...

    async def running_elsewhere(self) -> set[str]: ...

    async def release(self) -> None: ...

    async def until_free(self, session_id: str) -> None:
        """Until no other process holds ``session_id`` open."""
        ...


class AloneInTheStore:
    """No other process to share the owner with: a session it cannot open is
    held elsewhere, as its resume said.
    """

    async def acquire(self) -> bool:
        return True

    async def publish(self, session_ids: list[str]) -> None:
        return None

    async def running_elsewhere(self) -> set[str]:
        return set()

    async def release(self) -> None:
        return None

    async def until_free(self, session_id: str) -> None:
        await asyncio.get_running_loop().create_future()


@dataclass(frozen=True, slots=True)
class LeftTurn:
    session_id: str
    state: LeftTurnState


@dataclass(frozen=True, slots=True)
class LeftTurnsRead:
    status: LeftTurnsStatus
    turns: list[LeftTurn]


@dataclass(frozen=True, slots=True)
class LeftTurnsStopped:
    status: LeftTurnsStatus
    turns: list[StoppedLeftTurn]


@dataclass(frozen=True, slots=True)
class StoppedLeftTurn:
    session_id: str
    outcome: LeftTurnOutcome


def owner_key(client: ClientInfo, account: ClientAccount) -> Owner:
    return (
        client.entrypoint,
        account.organization_id,
        account.workspace_id,
        account.user_id,
    )


@dataclass
class _Entry:
    state: Literal[
        "waiting_for_lender",
        "queued",
        "resuming",
        "resumed",
        "ended",
        "failed",
        "running_elsewhere",
    ] = "queued"
    task: asyncio.Task[None] | None = None
    # While another process runs it: resumes it once that one lets it go.
    watch: asyncio.Task[None] | None = None


@dataclass
class _OwnerTurns:
    resume: ResumeOne
    sessions: ProcessSessions
    lender: LenderOf
    claim: OwnerClaim
    listed: asyncio.Event = field(default_factory=asyncio.Event)
    listing_failed: bool = False
    # Another process held the owner's claim when this one tried.
    elsewhere: bool = False
    # Taken by this process's listing, until its turns have settled.
    claimed: bool = False
    published: tuple[str, ...] | None = None
    retry: asyncio.Task[None] | None = None
    refresh: asyncio.Task[None] | None = None
    publishing: asyncio.Lock = field(default_factory=asyncio.Lock)
    entries: dict[str, _Entry] = field(default_factory=dict)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def resume_on_own_channel(
    serve: Callable[[JsonRpcTransport, str], Awaitable[None]],
    params: InitializeParams,
    session_id: str,
    interrupt: bool,
) -> None:
    """Resume ``session_id`` on a channel no client holds, then close it: a
    session still running is parked for the clients of the process.
    ``serve`` serves that channel as the process's own resume of the session.
    """
    client_side, server_side = memory_transport_pair()

    async def peer() -> None:
        await serve(server_side, session_id)

    client = AppServerClient(client_side, run_peer=peer)
    answering = asyncio.create_task(_answer_server_requests(client))
    try:
        await client.request("initialize", params)
        await client.notify("initialized", {})
        await client.request(
            "session/resume",
            SessionResumeParams(session_id=session_id, interrupt_left_turn=interrupt),
        )
    finally:
        answering.cancel()
        await client.close()


async def list_on_own_channel(
    serve: Callable[[JsonRpcTransport], Awaitable[None]], params: InitializeParams
) -> list[str]:
    """The sessions ``params``'s owner left mid-turn, listed on a channel no
    client holds: no client's channel, which may close meanwhile, is needed.
    """
    client_side, server_side = memory_transport_pair()

    async def peer() -> None:
        await serve(server_side)

    client = AppServerClient(client_side, run_peer=peer)
    answering = asyncio.create_task(_answer_server_requests(client))
    try:
        await client.request("initialize", params)
        await client.notify("initialized", {})
        listed: list[str] = []
        cursor: str | None = None
        while True:
            # Archived ones too, as clients resumed them before.
            page = SessionListResponse.model_validate(
                await client.request(
                    "session/list",
                    SessionListParams(
                        interrupted=True,
                        include_archived=True,
                        limit=100,
                        cursor=cursor,
                    ),
                )
            )
            listed.extend(item.id for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                return listed
    finally:
        answering.cancel()
        await client.close()


async def _answer_server_requests(client: AppServerClient) -> None:
    """No one answers a callback here: it stays open for a client that
    attaches. A credential is borrowed from another channel instead.
    """
    async for message in client.incoming():
        if (
            isinstance(message, ServerRequest)
            and message.method == "platformCredentials/resolve"
        ):
            await client.respond(
                message.id, {"status": "unavailable", "reason": "transient"}
            )


class LeftTurns:
    """Per owner: the sessions still to resume, being resumed, and resumed."""

    def __init__(self) -> None:
        self._owners: dict[Owner, _OwnerTurns] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._slots = asyncio.Semaphore(_CONCURRENCY)

    def resume_once(
        self,
        owner: Owner,
        list_left: ListLeft,
        resume: ResumeOne,
        sessions: ProcessSessions,
        lender: LenderOf,
        claim: OwnerClaim | None = None,
    ) -> None:
        """Resume ``owner``'s left turns, unless this process already does or
        another process holds the owner's ``claim``.
        """
        current = self._owners.get(owner)
        if current is not None and (not current.listed.is_set() or current.claimed):
            return
        if current is not None and current.retry is not None:
            current.retry.cancel()
        turns = _OwnerTurns(
            resume=resume,
            sessions=sessions,
            lender=lender,
            claim=AloneInTheStore() if claim is None else claim,
        )
        if current is not None and current.listing_failed:
            # A stop asked of the failed listing holds for this one too.
            turns.stop = current.stop
        if current is not None and not current.elsewhere:
            # Turns this process still carries on stay its own to read and stop.
            turns.entries = {
                session_id: entry
                for session_id, entry in current.entries.items()
                if sessions.parked(session_id) is not None
                or sessions.held_open(session_id)
            }
        self._owners[owner] = turns
        self._spawn(self._list(owner, turns, list_left))

    async def read(self, owner: Owner) -> LeftTurnsRead:
        turns = self._owners.get(owner)
        if turns is None:
            return LeftTurnsRead(status="not_resuming", turns=[])
        if turns.elsewhere:
            return LeftTurnsRead(
                status="resumed_elsewhere",
                turns=[
                    LeftTurn(session_id, "running_elsewhere")
                    for session_id in await self._running_elsewhere(turns)
                ],
            )
        listed: list[LeftTurn] = []
        sessions = turns.sessions
        for session_id, entry in turns.entries.items():
            # Outside its own resume, a turn reads as who holds it now.
            if (
                entry.state not in {"resuming", "ended"}
                and sessions.parked(session_id) is not None
            ):
                listed.append(LeftTurn(session_id, "running"))
                continue
            match entry.state:
                case (
                    "waiting_for_lender" | "queued" | "failed" | "running_elsewhere"
                ) if not sessions.held_open(session_id):
                    listed.append(LeftTurn(session_id, entry.state))
                # While its claim is held, only the process's own resume holds
                # the session; after, an open one is a client's.
                case "resuming" if sessions.resuming(
                    session_id
                ) or not sessions.held_open(session_id):
                    listed.append(LeftTurn(session_id, entry.state))
                case _:
                    pass
        return LeftTurnsRead(status=_status(turns), turns=listed)

    async def _running_elsewhere(self, turns: _OwnerTurns) -> list[str]:
        """Of the turns the claim's holder resumes, the ones a process holds
        open.
        """
        try:
            held = await turns.claim.running_elsewhere()
        except Exception:
            logger.warning("Could not read which sessions run elsewhere", exc_info=True)
            return []
        sessions = turns.sessions
        return sorted(
            session_id
            for session_id in held
            if sessions.parked(session_id) is None
            and not sessions.held_open(session_id)
        )

    async def interrupt(self, owner: Owner) -> LeftTurnsStopped:
        """End ``owner``'s left turns: a queued one as interrupted without
        running it, one running with no client by interrupting it. Each is
        answered once; a turn still running after its interrupt stays listed.
        A turn another process runs is left to that process.
        """
        turns = self._owners.get(owner)
        if turns is None:
            return LeftTurnsStopped(status="not_resuming", turns=[])
        async with turns.lock:
            turns.stop.set()
            try:
                await asyncio.wait_for(turns.listed.wait(), RESUME_TIMEOUT_S)
            except TimeoutError:
                return LeftTurnsStopped(status="listing_timed_out", turns=[])
            if turns.elsewhere:
                return LeftTurnsStopped(
                    status="resumed_elsewhere",
                    turns=[
                        StoppedLeftTurn(session_id, "running_elsewhere")
                        for session_id in await self._running_elsewhere(turns)
                    ],
                )
            if pending := _resuming(turns):
                await asyncio.wait(pending)
            stopped: list[StoppedLeftTurn] = []
            for session_id, entry in list(turns.entries.items()):
                outcome = await self._stop(turns, session_id, entry)
                if outcome == "running_elsewhere":
                    self._run_elsewhere(turns, session_id, entry)
                stopped.append(StoppedLeftTurn(session_id, outcome))
                if outcome not in {"still_running", "running_elsewhere"}:
                    del turns.entries[session_id]
                    if entry.watch is not None:
                        entry.watch.cancel()
            await self._settled(turns)
            return LeftTurnsStopped(status=_status(turns), turns=stopped)

    async def _stop(
        self, turns: _OwnerTurns, session_id: str, entry: _Entry
    ) -> LeftTurnOutcome:
        sessions = turns.sessions
        match entry.state:
            case "ended":
                return "ended_before_running"
            case "resumed":
                return await self._interrupt_resumed(turns, session_id, entry)
            case _ if sessions.parked(session_id) is not None or sessions.held_open(
                session_id
            ):
                # A chat took it since its resume failed: as read reports it.
                return await self._interrupt_resumed(turns, session_id, entry)
            case "running_elsewhere":
                return "running_elsewhere"
            case _:
                return "failed"

    async def _interrupt_resumed(
        self, turns: _OwnerTurns, session_id: str, entry: _Entry
    ) -> LeftTurnOutcome:
        sessions = turns.sessions
        if sessions.parked(session_id) is None:
            if sessions.held_open(session_id):
                return "taken_by_client"
            return "already_ended"
        resumed = await self._resume(turns, session_id, interrupt=True, adopt=False)
        if resumed in {"taken", "ended"}:
            return "taken_by_client" if resumed == "taken" else "already_ended"
        if sessions.parked(session_id) is not None:
            return "still_running"
        return _STOPPED_BY.get(resumed, "interrupted")

    async def _list(
        self, owner: Owner, turns: _OwnerTurns, list_left: ListLeft
    ) -> None:
        try:
            claimed = await turns.claim.acquire()
            session_ids = (
                await asyncio.wait_for(list_left(), RESUME_TIMEOUT_S) if claimed else []
            )
        except Exception:
            logger.warning("Could not list the turns left mid-flight", exc_info=True)
            # The next channel of the owner lists again, here or elsewhere.
            await self._release(turns)
            turns.listing_failed = True
            turns.listed.set()
            return
        if not claimed:
            logger.info("Another process resumes the turns this owner left")
            turns.elsewhere = True
            turns.listed.set()
            turns.retry = self._spawn(self._take_over(owner, turns, list_left))
            return
        turns.claimed = True
        session_ids = [
            session_id for session_id in session_ids if session_id not in turns.entries
        ]
        if session_ids:
            logger.info("Resuming sessions left mid-turn: %s", ", ".join(session_ids))
        for session_id in session_ids:
            entry = _Entry()
            turns.entries[session_id] = entry
            entry.task = self._spawn(self._resume_entry(turns, session_id, entry))
        turns.listed.set()
        await self._settled(turns)
        if turns.claimed:
            turns.refresh = self._spawn(self._refresh(turns))

    async def _refresh(self, turns: _OwnerTurns) -> None:
        # A parked turn ends on its own: what is published follows it.
        while turns.claimed:
            await asyncio.sleep(_CLAIM_RETRY_S)
            await self._settled(turns)

    async def _take_over(
        self, owner: Owner, turns: _OwnerTurns, list_left: ListLeft
    ) -> None:
        """Once the holder lets the owner's claim go, list and resume here."""
        try:
            while True:
                await asyncio.sleep(_CLAIM_RETRY_S)
                try:
                    if await turns.claim.acquire():
                        break
                except Exception:
                    logger.warning("Could not take a left-turns claim", exc_info=True)
        except asyncio.CancelledError:
            # Superseded or closing: a claim it took meanwhile is let go.
            await asyncio.shield(self._release(turns))
            raise
        if self._owners.get(owner) is not turns:
            await self._release(turns)
            return
        turns.retry = None
        self.resume_once(
            owner, list_left, turns.resume, turns.sessions, turns.lender, turns.claim
        )

    async def _settled(self, turns: _OwnerTurns) -> None:
        """Publish the turns this process runs; once none is still being
        resumed or waited on, let the owner's claim go so the next opt-in, here
        or in another process, lists again.
        """
        async with turns.publishing:
            await self._publish_or_release(turns)

    async def _publish_or_release(self, turns: _OwnerTurns) -> None:
        if not turns.claimed:
            return
        sessions = turns.sessions
        running = tuple(
            sorted(
                session_id
                for session_id, entry in turns.entries.items()
                if entry.state == "resuming"
                or (
                    entry.state == "resumed" and sessions.parked(session_id) is not None
                )
            )
        )
        unsettled = any(
            entry.state in {"waiting_for_lender", "queued", "resuming"}
            or (entry.state == "running_elsewhere" and entry.watch is not None)
            for entry in turns.entries.values()
        )
        published = () if not unsettled else running
        if published != turns.published:
            try:
                await turns.claim.publish(list(published))
                turns.published = published
            except Exception:
                logger.warning(
                    "Could not publish the left turns resumed", exc_info=True
                )
        if not unsettled:
            turns.claimed = False
            turns.published = None
            if (
                turns.refresh is not None
                and turns.refresh is not asyncio.current_task()
            ):
                turns.refresh.cancel()
            await self._release(turns)

    async def _resume_entry(
        self, turns: _OwnerTurns, session_id: str, entry: _Entry
    ) -> None:
        while True:
            await self._wait_for_lender(turns, entry)
            entry.state = "queued"
            async with self._slots:
                # The lender may have left while this waited for a slot.
                if not turns.stop.is_set() and turns.lender.availability() != (
                    "durable"
                ):
                    continue
                await self._resume_held(turns, session_id, entry)
                await self._settled(turns)
                return

    async def _resume_held(
        self, turns: _OwnerTurns, session_id: str, entry: _Entry
    ) -> None:
        interrupt = turns.stop.is_set()
        entry.state = "resuming"
        await self._settled(turns)
        match await self._resume(turns, session_id, interrupt=interrupt, adopt=True):
            case "resumed":
                # An interrupted turn can run on, parked as its channel closed.
                ended = interrupt and turns.sessions.parked(session_id) is None
                entry.state = "ended" if ended else "resumed"
            case "adopted" | "taken":
                # Read and stop go by who holds it when they are asked.
                entry.state = "resumed"
            case "failed":
                # A resume cut after its turn started left it parked, or a
                # client opened it meanwhile: read and stop go by who holds it.
                sessions = turns.sessions
                held = sessions.parked(session_id) is not None or sessions.held_open(
                    session_id
                )
                entry.state = "resumed" if held else "failed"
            case "busy":
                self._run_elsewhere(turns, session_id, entry)
            case "ended":
                raise AssertionError("Only a stop's own resume finds its turn ended")

    async def _resume(
        self, turns: _OwnerTurns, session_id: str, *, interrupt: bool, adopt: bool
    ) -> _Resumed:
        sessions = turns.sessions
        # One deadline for the claim and the resume: a client resume waiting
        # behind it waits as long, in all.
        deadline = asyncio.get_running_loop().time() + RESUME_TIMEOUT_S
        try:
            async with asyncio.timeout_at(deadline):
                claim = await sessions.begin_resume(session_id)
        except TimeoutError:
            logger.warning("Session %s left mid-turn stayed busy", session_id)
            return "failed"
        # A client may have opened it while this waited for the claim. A
        # parked session's own host still holds it open.
        parked = sessions.parked(session_id) is not None
        outcome: _Resumed | None = None
        if not parked and sessions.held_open(session_id):
            outcome = "taken"
        elif parked and adopt:
            outcome = "adopted"
        elif not parked and not adopt:
            outcome = "ended"
        if outcome is not None:
            sessions.end_resume(session_id, claim)
            return outcome
        try:
            async with asyncio.timeout_at(deadline):
                await turns.resume(session_id, interrupt)
        except AppServerResponseError as exc:
            if not _is_busy(exc):
                logger.warning(
                    "Could not resume session %s left mid-turn",
                    session_id,
                    exc_info=True,
                )
                return "failed"
            logger.info("Session %s left mid-turn runs elsewhere", session_id)
            return "busy"
        except Exception:
            logger.warning(
                "Could not resume session %s left mid-turn", session_id, exc_info=True
            )
            return "failed"
        finally:
            sessions.end_resume(session_id, claim)
        return "resumed"

    def _run_elsewhere(
        self, turns: _OwnerTurns, session_id: str, entry: _Entry
    ) -> None:
        entry.state = "running_elsewhere"
        if entry.watch is None or entry.watch.done():
            entry.watch = self._spawn(self._resume_when_free(turns, session_id, entry))

    async def _resume_when_free(
        self, turns: _OwnerTurns, session_id: str, entry: _Entry
    ) -> None:
        """Once the other process lets the session go, its turn is left again:
        resume it, or end it if a stop was asked meanwhile.
        """
        failing = False
        while entry.state == "running_elsewhere":
            try:
                await turns.claim.until_free(session_id)
            except Exception:
                if not failing:
                    logger.warning(
                        "Could not watch session %s run elsewhere",
                        session_id,
                        exc_info=True,
                    )
                failing = True
                await asyncio.sleep(_WATCH_RETRY_S)
                continue
            # Not inside a stop: it answers the turn as it finds it, and a
            # stop after waits for this resume.
            async with turns.lock:
                if turns.entries.get(session_id) is not entry:
                    return
                entry.state = "queued"
                entry.task = self._spawn(self._resume_entry(turns, session_id, entry))
            # Busy again, it reads as running elsewhere, and this watches on.
            await asyncio.shield(entry.task)

    async def _wait_for_lender(self, turns: _OwnerTurns, entry: _Entry) -> None:
        if turns.stop.is_set():
            return
        stopped = asyncio.ensure_future(turns.stop.wait())
        try:
            while not turns.stop.is_set():
                # Taken before the check, so no change between them is lost.
                changed = turns.lender.changed()
                if turns.lender.availability() == "durable":
                    changed.cancel()
                    return
                entry.state = "waiting_for_lender"
                try:
                    await asyncio.wait(
                        {changed, stopped}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    changed.cancel()
        finally:
            stopped.cancel()

    def _spawn(self, work: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def stop_resuming(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def close(self) -> None:
        await self.stop_resuming()
        for turns in self._owners.values():
            await self._release(turns)

    async def _release(self, turns: _OwnerTurns) -> None:
        try:
            await turns.claim.release()
        except Exception:
            logger.warning("Could not release a left-turns claim", exc_info=True)


_STOPPED_BY: dict[_Resumed, LeftTurnOutcome] = {
    "failed": "failed",
    "busy": "running_elsewhere",
}


def _is_busy(exc: AppServerResponseError) -> bool:
    data = exc.error.data
    return (
        exc.error.code == ProtocolErrorCode.CONFLICT
        and isinstance(data, dict)
        and data.get("harnessCode") == "session_busy"
    )


def _resuming(turns: _OwnerTurns) -> list[asyncio.Task[None]]:
    return [
        entry.task
        for entry in turns.entries.values()
        if entry.task is not None and not entry.task.done()
    ]


def _status(turns: _OwnerTurns) -> LeftTurnsStatus:
    if not turns.listed.is_set():
        return "listing"
    if turns.elsewhere:
        return "resumed_elsewhere"
    return "listing_failed" if turns.listing_failed else "listed"


async def close_left_turns_first(
    left_turns: LeftTurns, open_sessions: OpenSessions
) -> None:
    """A resume cut by the exit parks its session as its channel closes: stop
    parked sessions only after that. The owner's claim goes last: until those
    sessions stop, another process taking it would find them busy.
    """
    await left_turns.stop_resuming()
    await open_sessions.close()
    await left_turns.close()


__all__ = [
    "AccountLender",
    "AloneInTheStore",
    "LeftTurn",
    "LeftTurnOutcome",
    "LeftTurnState",
    "LeftTurns",
    "LeftTurnsRead",
    "LeftTurnsStatus",
    "LeftTurnsStopped",
    "LenderOf",
    "ListOnOwnChannel",
    "OpenLeftTurnChannel",
    "Owner",
    "OwnerClaim",
    "ProcessSessions",
    "ResumeClaim",
    "ResumeOne",
    "SignsInItself",
    "StoppedLeftTurn",
    "close_left_turns_first",
    "list_on_own_channel",
    "owner_key",
    "resume_on_own_channel",
]
