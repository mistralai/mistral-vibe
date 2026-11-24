"""The target runtime's Session API refusals, as the Runtime errors the adapter maps.

A Session on the target runtime refuses a command with the target's own Session
API error classes. The adapter maps Runtime errors by their code, so each
refusal it can act on becomes the Runtime error the local runtime raises for the
same case, and the client gets the same error as on a local Session.

Only a connection to the target runtime imports this module: the target classes
it names are not in every Harness release Vibe accepts.

Left as they are, so the client gets an internal error:

- a callback ID the target does not know: the target raises an untyped error,
  which only its text tells apart, and Vibe does not guess from text;
- every other error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from mistralai.vibe.harness.app_server.session.transport import (
    SessionBusyError,
    SessionCallbackRejectedError,
    SessionCallbackSettledError,
    SessionIdempotencyConflictError,
    SessionTurnActiveError,
    SessionTurnConflictError,
)

from mistralai_vibe_local_harness.vibe import (
    HarnessSessionError,
    HarnessStaleTurnError,
    HarnessTurnConflictError,
    HarnessTurnQueueIdempotencyConflictError,
)


@asynccontextmanager
async def target_refusals(
    active_turn_id: Callable[[], Awaitable[str | None]],
) -> AsyncIterator[None]:
    """Raise a target Session's refusals as the matching Runtime errors.

    The target's Turn conflict (a steer or interrupt that names another Turn)
    carries no active Turn ID, so ``active_turn_id`` reads it back. Its settled
    callback error is raised only for a callback closed or timed out; an
    already answered one is accepted again.
    """
    try:
        yield
    except SessionTurnActiveError as exc:
        raise HarnessTurnConflictError(exc.active_turn_id) from exc
    except SessionBusyError as exc:
        raise HarnessSessionError("session_busy", str(exc)) from exc
    except SessionTurnConflictError as exc:
        raise HarnessStaleTurnError(await active_turn_id()) from exc
    except SessionIdempotencyConflictError as exc:
        raise HarnessTurnQueueIdempotencyConflictError(exc.idempotency_key) from exc
    except SessionCallbackRejectedError as exc:
        raise HarnessSessionError("callback_rejected", str(exc)) from exc
    except SessionCallbackSettledError as exc:
        raise HarnessSessionError("callback_closed", str(exc)) from exc


__all__ = ["target_refusals"]
