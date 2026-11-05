"""Transport contract for the stateless Session Protocol."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from .models.base import SessionModel
from .models.events import ClientTransportStreamItem
from .procedures.session import CallbacksTreeParams, CallbacksTreeResponse, EventsReadParams


class UnsupportedSessionProcedureError(RuntimeError):
    """A Session transport cannot implement one procedure without changing its contract."""

    def __init__(
        self,
        *,
        method: str,
        backend: str,
        reason: str,
    ) -> None:
        self.method = method
        self.backend = backend
        self.reason = reason
        super().__init__(
            f"Session procedure {method!r} is unsupported by backend {backend!r}: {reason}"
        )


CHILD_SESSION_PUBLIC_ACCESS = (
    "a client may read a subagent's session, answer its callbacks, rename or archive it, "
    "and interrupt one exact running turn; to redirect a subagent, message or steer its parent"
)

CHILD_SESSION_COMMAND_REFUSED = "child_session_command_refused"
"""The wire code of a served refusal of a client command on a subagent's session.

The Vibe Agents API sends it as the `detail.code` of a 403, and the SDK transport turns it into
`ChildSessionProcedureRefusedError`.
"""


class ChildSessionProcedureRefusedError(UnsupportedSessionProcedureError):
    """A client asked a subagent's session for something only its parent agent may do.

    Starting, steering, or messaging a subagent belongs to its parent agent. Every host
    refuses these procedures with this error before it applies anything.
    """

    def __init__(
        self,
        *,
        method: str,
        backend: str,
        reason: str = CHILD_SESSION_PUBLIC_ACCESS,
    ) -> None:
        super().__init__(method=method, backend=backend, reason=reason)
        self.args = (f"Session procedure {method!r} is refused on a subagent's session: {reason}",)


class SessionTurnConflictError(RuntimeError):
    """A Turn command named a Turn that is not the Session's active Turn.

    The Turn already ended, or another Turn is running. Nothing was applied.
    Read the Session to find its active Turn.
    """


class SessionTerminatedError(RuntimeError):
    """The Session is closed or terminal and accepts no further procedures."""


class SessionIdempotencyConflictError(RuntimeError):
    """An idempotency key was reused with different input."""

    def __init__(self, *, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"Idempotency key {idempotency_key!r} was reused with different input")


class SessionBusyError(RuntimeError):
    """The Session cannot accept the message yet and committed nothing.

    Each transport retries for a bounded time before it raises this error.
    Retry the same request later.
    """


class SessionTurnActiveError(SessionBusyError):
    """A Turn start was refused because another Turn of the Session is running.

    Nothing was applied. ``active_turn_id`` is the running Turn. The same idempotency key
    starts a Turn once that Turn ends.
    """

    def __init__(self, message: str, *, active_turn_id: str) -> None:
        super().__init__(message)
        self.active_turn_id = active_turn_id


class SessionCallbackSettledError(RuntimeError):
    """The callback was already resolved or cancelled, so this answer changed nothing.

    Another attachment may have answered first. The answer is not applied.
    """


class SessionCallbackRejectedError(RuntimeError):
    """The Session refused this answer and applied nothing.

    The callback stays open, so answer it again with a corrected result under the same
    callback id.
    """


class SessionMessageRejectedError(RuntimeError):
    """The Session refused this message and committed nothing.

    Do not retry it unchanged. Send a corrected message with a new idempotency key.
    """


class SessionEventStreamLostError(RuntimeError):
    """The event stream broke and the transport could not resume it.

    A lost stream changes nothing on the server: the Session and its active
    Turn may still be running. `after_event_id` is the last event the client
    received. Call `SessionClient.events()` on the same client to resume after
    it, or `SessionClient.read_session()` to read the current state.
    """

    def __init__(
        self,
        *,
        session_id: str,
        after_event_id: str | None,
        reason: str,
        reattach_hint: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.after_event_id = after_event_id
        self.reason = reason
        hint = reattach_hint or (
            "call SessionClient.events() on this client to resume after the last received "
            "event, or SessionClient.read_session() to read the current state"
        )
        super().__init__(
            f"Lost the event stream of Session {session_id}: {reason}. Nothing was "
            "interrupted: the Session and its active Turn may still be running. To follow it "
            f"again, {hint}. Last received event: {after_event_id!r}."
        )


@runtime_checkable
class SessionTransport(Protocol):
    """Send typed Session procedures and read one session's ordered event stream."""

    async def request[ResultT: SessionModel](
        self,
        method: str,
        params: SessionModel,
        result_type: type[ResultT],
    ) -> ResultT: ...

    def read_events(self, params: EventsReadParams) -> AsyncIterator[ClientTransportStreamItem]: ...

    def watch_callbacks_tree(
        self, params: CallbacksTreeParams
    ) -> AsyncIterator[CallbacksTreeResponse]:
        """Follow `app_server/session/callbacks/tree`: the full list now, then again on each change.

        Transports that cannot follow the list raise `UnsupportedSessionProcedureError`.
        """
        raise UnsupportedSessionProcedureError(
            method="app_server/session/callbacks/tree",
            backend=type(self).__name__,
            reason="this transport cannot follow the callbacks of a Session tree",
        )

    async def close(self) -> None: ...


__all__ = [
    "CHILD_SESSION_PUBLIC_ACCESS",
    "ChildSessionProcedureRefusedError",
    "SessionBusyError",
    "SessionCallbackRejectedError",
    "SessionCallbackSettledError",
    "SessionEventStreamLostError",
    "SessionIdempotencyConflictError",
    "SessionMessageRejectedError",
    "SessionTerminatedError",
    "SessionTransport",
    "SessionTurnActiveError",
    "SessionTurnConflictError",
    "UnsupportedSessionProcedureError",
]
