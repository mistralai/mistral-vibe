"""Map Unified Harness errors to the app server's session backend errors.

The adapter and the Session API path both call the Harness, so the mapping lives
here, outside both.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Never

from mistralai_vibe_local_harness.vibe import (
    HarnessNotImplementedError,
    HarnessSessionError,
    HarnessSessionNotFoundError,
)
from vibe.app_server._error_message_cap import bounded_error_message
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.protocol import ProtocolErrorCode


async def harness_call[ResultT](operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except HarnessSessionNotFoundError as exc:
        raise SessionBackendError(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
    except HarnessNotImplementedError as exc:
        raise SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
    except HarnessSessionError as exc:
        raise_session_backend_error(exc)
    except ValueError as exc:
        raise SessionBackendError(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc


_DIRECT_SESSION_ERROR_CODES: dict[str, ProtocolErrorCode] = {
    "callback_closed": ProtocolErrorCode.CALLBACK_CLOSED,
    "callback_not_found": ProtocolErrorCode.NOT_FOUND,
    "unknown_process": ProtocolErrorCode.NOT_FOUND,
    "callback_conflict": ProtocolErrorCode.CONFLICT,
    "callback_rejected": ProtocolErrorCode.INVALID_PARAMS,
    "configuration_rejected": ProtocolErrorCode.INVALID_PARAMS,
    "history_entry_conflict": ProtocolErrorCode.CONFLICT,
    "turn_queue_edit_conflict": ProtocolErrorCode.CONFLICT,
}


# Internal harness errors the client explains to the user: not Vibe bugs.
_EXPLAINED_HARNESS_CODES = frozenset({"store_requires_newer_reader"})


def raise_session_backend_error(exc: HarnessSessionError) -> Never:
    direct_code = _DIRECT_SESSION_ERROR_CODES.get(exc.code)
    if direct_code is not None:
        raise SessionBackendError(direct_code, str(exc)) from exc
    if exc.code == "context_compaction_failed":
        data: dict[str, Any] = {"reason": "context_compaction_failed"}
        if exc.details is not None:
            reason = exc.details.get("reason")
            if isinstance(reason, str):
                data["reason"] = reason
            details = exc.details.get("details")
            if details is not None:
                data["details"] = details
        raise SessionBackendError(
            ProtocolErrorCode.COMPACTION_FAILED,
            bounded_error_message(str(exc)),
            data,
            after_response=getattr(exc, "after_response", None),
            on_response_abandoned=getattr(exc, "on_response_abandoned", None),
        ) from exc
    if exc.code == "stale_turn":
        data: dict[str, Any] = {}
        active_turn_id = exc.details.get("active_turn_id") if exc.details else None
        if active_turn_id is None:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "No active turn"
            ) from exc
        data["activeTurnId"] = active_turn_id
        raise SessionBackendError(
            ProtocolErrorCode.STALE_TURN, str(exc), data or None
        ) from exc
    if exc.code == "turn_queue_full":
        max_items = exc.details.get("max_items") if exc.details else None
        raise SessionBackendError(
            ProtocolErrorCode.CONFLICT, str(exc), {"maxItems": max_items}
        ) from exc
    if exc.code == "turn_queue_idempotency_conflict":
        idempotency_key = exc.details.get("idempotency_key") if exc.details else None
        raise SessionBackendError(
            ProtocolErrorCode.CONFLICT, str(exc), {"idempotencyKey": idempotency_key}
        ) from exc
    if exc.code == "turn_queue_item_not_found":
        queue_item_id = exc.details.get("queue_item_id") if exc.details else None
        raise SessionBackendError(
            ProtocolErrorCode.NOT_FOUND, str(exc), {"queueItemId": queue_item_id}
        ) from exc
    if exc.code == "invalid_cursor":
        raise SessionBackendError(
            ProtocolErrorCode.INVALID_PARAMS, str(exc), exc.details
        ) from exc
    code = {
        "child_session_requires_parent": ProtocolErrorCode.CONFLICT,
        "client_command_conflict": ProtocolErrorCode.CONFLICT,
        "fork_active_turn": ProtocolErrorCode.CONFLICT,
        "fork_entry_not_found": ProtocolErrorCode.INVALID_PARAMS,
        "fork_entry_required": ProtocolErrorCode.CONFLICT,
        "session_busy": ProtocolErrorCode.CONFLICT,
        "turn_conflict": ProtocolErrorCode.CONFLICT,
        "turn_queue_pending": ProtocolErrorCode.CONFLICT,
        "unfinished_work_migration": ProtocolErrorCode.CONFLICT,
    }.get(exc.code, ProtocolErrorCode.INTERNAL_ERROR)
    data = {"harnessCode": exc.code}
    if exc.details is not None:
        data["details"] = exc.details
        reason = exc.details.get("reason")
        if isinstance(reason, str):
            data["reason"] = reason
    raise SessionBackendError(
        code, str(exc), data, expected=exc.code in _EXPLAINED_HARNESS_CODES
    ) from exc


def raise_unsupported(operation: str) -> Never:
    raise SessionBackendError(
        ProtocolErrorCode.INTERNAL_ERROR,
        f"The Unified Harness backend does not implement {operation} yet.",
    )


__all__ = ["harness_call", "raise_session_backend_error", "raise_unsupported"]
