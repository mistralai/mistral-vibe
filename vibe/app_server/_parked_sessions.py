"""A session whose channel closes while its turn runs keeps running, parked in
the process's ``OpenSessions``, and a ``session/resume`` on another channel of
the process attaches to it.

A session runs on its channel's ``ChannelRuntime`` (host, link, catalogs) and
reaches its client only through the link. Attaching swaps the two channels'
runtimes and repoints the link, so the live session moves as a whole; the
attaching channel's own, unused host goes to the closed channel and is shut
down.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from vibe.app_server import _left_turns
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._execution import cancel_tasks
from vibe.app_server._platform_credential_lenders import AccountKey, account_key
from vibe.app_server._session_backend_port import (
    SessionBackend,
    SessionBackendError,
    SessionBackendHostClientLoss,
    SessionBackendTurnWork,
)
from vibe.app_server.models import BlockedSessionStatus, RunningSessionStatus
from vibe.app_server.protocol import (
    ClientCapabilities,
    ProtocolErrorCode,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    TurnInterruptParams,
)

if TYPE_CHECKING:
    from vibe.app_server._open_sessions import OpenSessions, ParkedSession
    from vibe.app_server.server import AppServer

logger = logging.getLogger(__name__)

_INTERRUPT_SETTLE_S = 5.0
_INTERRUPT_POLL_S = 0.05


class ParkedChannel:
    def __init__(self, server: AppServer) -> None:
        self.server = server

    def has_turn_work(self) -> bool:
        root = self.server._root
        return isinstance(root, SessionBackendTurnWork) and root.has_turn_work

    async def stop(self) -> None:
        await self.server._close_root()


def park_running_session(server: AppServer, open_sessions: OpenSessions) -> bool:
    root = server._root
    if not isinstance(root, SessionBackendTurnWork) or not root.has_turn_work:
        return False
    open_sessions.park(root.session_id, ParkedChannel(server))
    return True


def _account(server: AppServer) -> AccountKey | None:
    account = server._client_account
    return None if account is None else account_key(account)


def _still_busy(session_id: str) -> RequestFailure:
    # As the store answers a session another holder has open.
    return RequestFailure(
        ProtocolErrorCode.CONFLICT,
        f"Session is already open: {session_id}",
        {"harnessCode": "session_busy", "details": {"session_id": session_id}},
    )


async def _settled(
    server: AppServer, open_sessions: OpenSessions, session_id: str
) -> ParkedSession | None:
    """The process's own resume of it, a stop or another attach in flight
    settles first: the session is then parked again, attached elsewhere
    (busy), or stopped (opened from the store). The waits share the deadline
    the process's own resume has, so a resume that overruns it fails busy.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _left_turns.RESUME_TIMEOUT_S
    try:
        async with asyncio.timeout_at(deadline):
            while True:
                if (
                    open_sessions.resuming(session_id)
                    and server._left_turn_session != session_id
                ):
                    await open_sessions.wait_resumed(session_id)
                    continue
                if (parked := open_sessions.parked(session_id)) is not None:
                    return parked
                if not open_sessions.settling(session_id):
                    return None
                await open_sessions.wait_stopped(session_id)
    except TimeoutError:
        raise _still_busy(session_id) from None


async def attach_parked(
    server: AppServer, open_sessions: OpenSessions, params: SessionResumeParams
) -> SessionReadResponse | None:
    """The parked session attached to ``server``; None when no closed channel
    left it running. The session runs on as it is: only the history page and
    ``interruptLeftTurn`` of the resume apply.
    """
    session_id = params.session_id
    while True:
        parked = await _settled(server, open_sessions, session_id)
        if parked is None or not isinstance(parked, ParkedChannel):
            return None
        holder = parked.server
        root = holder._root
        if root is None:
            return None
        # Checked while it is still parked: a refused attach must leave it so.
        await _check_attach(server, holder, root, params)
        # Another attach, or the process's own resume, may have taken it
        # during the checks: look again.
        if open_sessions.parked(session_id) is parked and (
            not open_sessions.resuming(session_id)
            or server._left_turn_session == session_id
        ):
            open_sessions.take_parked(session_id)
            break
    if params.interrupt_left_turn:
        try:
            await interrupt_running_turn(root)
            # What the interrupt left open must be answerable here too.
            await server._validate_open_callback_capabilities("session/resume", root)
        except BaseException:
            open_sessions.park(params.session_id, parked)
            raise
    watches_artifacts = server._unsubscribe_artifacts is not None
    server._stop_artifact_subscription()
    _swap_runtime(server, holder)
    holder._root = None
    try:
        response = await server._activate_lifecycle_backend(
            "session/resume", root, params.history_limit
        )
    except BaseException:
        backend_events = server._backend_event_task
        server._backend_event_task = None
        server._backend_event_backend = None
        if backend_events is not None:
            await cancel_tasks([backend_events], label="unattached session events")
        server._root = None
        _swap_runtime(server, holder)
        holder._root = root
        open_sessions.park(params.session_id, parked)
        if watches_artifacts:
            server._subscribe_artifacts()
        raise
    open_sessions.attached(params.session_id)
    if isinstance(server._session_backend_host, SessionBackendHostClientLoss):
        server._session_backend_host.mark_client_reconnected()
    if watches_artifacts:
        server._subscribe_artifacts()
    try:
        await holder._session_backend_host.shutdown()
    except Exception:
        logger.warning("Could not shut down an unused session host", exc_info=True)
    return response


async def interrupt_running_turn(root: SessionBackend) -> None:
    """End the running turn, and wait a bounded time for it to settle, so the
    attach that follows shows it ended.
    """
    state = await root.read(
        SessionReadParams(session_id=root.session_id, history=None, turns=None)
    )
    status = state.state.session.status
    if not isinstance(status, RunningSessionStatus | BlockedSessionStatus):
        return
    try:
        result = await root.interrupt_turn(
            TurnInterruptParams(
                session_id=root.session_id, expected_turn_id=status.active_turn_id
            )
        )
    except SessionBackendError as exc:
        # The turn may have ended while the session attached; any other
        # failure fails the attach, which parks the session again.
        if exc.code not in {ProtocolErrorCode.STALE_TURN, ProtocolErrorCode.CONFLICT}:
            raise
        logger.info("The turn of session %s ended first", root.session_id)
        return
    if result.after_response is not None:
        result.after_response()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _INTERRUPT_SETTLE_S
    # Only the interrupted turn: a follow-up queued behind it may run on.
    while await _active_turn(root) == status.active_turn_id:
        if loop.time() >= deadline:
            logger.warning("The interrupted turn of %s did not settle", root.session_id)
            return
        await asyncio.sleep(_INTERRUPT_POLL_S)


async def _active_turn(root: SessionBackend) -> str | None:
    state = await root.read(
        SessionReadParams(session_id=root.session_id, history=None, turns=None)
    )
    status = state.state.session.status
    if isinstance(status, RunningSessionStatus | BlockedSessionStatus):
        return status.active_turn_id
    return None


async def _check_attach(
    server: AppServer,
    holder: AppServer,
    root: SessionBackend,
    params: SessionResumeParams,
) -> None:
    if server._root is not None:
        raise RequestFailure(
            ProtocolErrorCode.CONFLICT,
            "Resume a session that runs with no connection on a connection of its own",
        )
    if _client_tools(holder._client_capabilities) != _client_tools(
        server._client_capabilities
    ):
        raise RequestFailure(
            ProtocolErrorCode.CONFLICT,
            "The session runs with client tools this connection did not declare",
        )
    if _account(holder) != _account(server):
        raise RequestFailure(
            ProtocolErrorCode.CONFLICT,
            "The session runs for another account than this connection's",
        )
    if not params.interrupt_left_turn:
        await server._validate_open_callback_capabilities("session/resume", root)


def _swap_runtime(server: AppServer, other: AppServer) -> None:
    server._runtime, other._runtime = other._runtime, server._runtime
    server._runtime.link.server = server
    other._runtime.link.server = other


def _client_tools(
    capabilities: ClientCapabilities,
) -> tuple[list[str], list[str], list[str]]:
    return (
        sorted(capabilities.client_tools),
        sorted(plugin.model_dump_json() for plugin in capabilities.agent_plugins),
        sorted(tool.model_dump_json() for tool in capabilities.client_tool_definitions),
    )


__all__ = [
    "ParkedChannel",
    "attach_parked",
    "interrupt_running_turn",
    "park_running_session",
]
