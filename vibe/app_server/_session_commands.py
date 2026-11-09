"""The commands the adapter sends to a Session, on either path.

``SessionCommands`` is what the adapter calls: Turn start, steer and interrupt,
callback answers and configuration pushes. ``DirectCommands`` sends them to the
Runtime session directly; ``SessionApiCommands`` sends them through the Session
API. This module imports nothing from the Session API, so a Session on the
direct path does not load it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import (
        SessionConfig,
        UnifiedHarnessSessionBackend,
        UnifiedHarnessSessionBackendHost,
    )
    from vibe.app_server.protocol import (
        CallbackResultParams,
        TurnInterruptParams,
        TurnStartParams,
        TurnSteerParams,
    )


@dataclass(frozen=True, slots=True)
class StartedTurn:
    """The Turn a Turn start accepted."""

    id: str
    session_id: str
    started_at: int


@dataclass(frozen=True, slots=True)
class Accepted:
    """The reply to a steer, an interrupt or a callback answer.

    ``last_event_id`` is the Runtime's event watermark, or None when the reply
    names no client event ID and the adapter uses its own.
    """

    accepted: Literal[True]
    last_event_id: int | None


@dataclass(frozen=True, slots=True)
class CommandResult[ReplyT]:
    """A command reply: the response and the callbacks around sending it."""

    response: ReplyT
    after_response: Callable[[], None] | None = None
    on_response_abandoned: Callable[[], None] | None = None


class SessionCommands(Protocol):
    """The commands the adapter sends to one root Session and its subagents."""

    async def start_turn(
        self, params: TurnStartParams
    ) -> CommandResult[StartedTurn]: ...

    async def steer_turn(self, params: TurnSteerParams) -> CommandResult[Accepted]: ...

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> CommandResult[Accepted]: ...

    async def respond_to_callback(
        self, params: CallbackResultParams
    ) -> CommandResult[Accepted]: ...

    async def apply_config(
        self, config: SessionConfig, *, allow_reserved_turn: bool = False
    ) -> None: ...

    async def close(self) -> None: ...


class DirectCommands(SessionCommands):
    """The commands sent to the Runtime session, without the Session API.

    A callback answer goes through the Host when there is one, so an answer for
    a subagent reaches that subagent's Session.
    """

    def __init__(
        self,
        session: UnifiedHarnessSessionBackend,
        host: UnifiedHarnessSessionBackendHost | None = None,
    ) -> None:
        self._session = session
        self._host = host

    async def start_turn(self, params: TurnStartParams) -> CommandResult[StartedTurn]:
        return turn_started(await self._session.start_turn(params))

    async def steer_turn(self, params: TurnSteerParams) -> CommandResult[Accepted]:
        return accepted(await self._session.steer_turn(params))

    async def interrupt_turn(
        self, params: TurnInterruptParams
    ) -> CommandResult[Accepted]:
        return accepted(await self._session.interrupt_turn(params))

    async def respond_to_callback(
        self, params: CallbackResultParams
    ) -> CommandResult[Accepted]:
        if self._host is not None:
            return accepted(
                await self._host.respond_to_callback(self._session.session_id, params)
            )
        return accepted(await self._session.respond_to_callback(params))

    async def apply_config(
        self, config: SessionConfig, *, allow_reserved_turn: bool = False
    ) -> None:
        await self._session.apply_config(
            config, allow_reserved_turn=allow_reserved_turn
        )

    async def close(self) -> None:
        """Nothing to close: the adapter shuts the Runtime session down itself."""


def turn_started(reply: object) -> CommandResult[StartedTurn]:
    """A Runtime Turn start reply as a typed ``CommandResult``.

    The Runtime session types its command replies as ``object``. A Turn start's
    response holds the accepted ``turn``.
    """
    raw = cast(Any, reply)
    turn = raw.response.turn
    return CommandResult(
        response=StartedTurn(
            id=turn.id, session_id=turn.session_id, started_at=turn.started_at
        ),
        after_response=raw.after_response,
        on_response_abandoned=raw.on_response_abandoned,
    )


def accepted(reply: object) -> CommandResult[Accepted]:
    """A Runtime steer, interrupt or callback reply as a typed ``CommandResult``.

    The Runtime session and the Host type their replies as ``object``; the
    response is a mapping with ``accepted`` and the Runtime's ``last_event_id``.
    """
    raw = cast(Any, reply)
    return CommandResult(
        response=Accepted(
            accepted=raw.response["accepted"],
            last_event_id=raw.response["last_event_id"],
        ),
        after_response=raw.after_response,
        on_response_abandoned=raw.on_response_abandoned,
    )


__all__ = [
    "Accepted",
    "CommandResult",
    "DirectCommands",
    "SessionCommands",
    "StartedTurn",
    "accepted",
    "turn_started",
]
