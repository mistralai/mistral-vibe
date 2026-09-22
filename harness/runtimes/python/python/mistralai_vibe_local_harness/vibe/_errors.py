"""Errors shared by the Vibe Harness Host and its Sessions."""

from collections.abc import Callable
from typing import Any


class HarnessSessionError(RuntimeError):
    """Dependency-neutral failure exposed by the Harness Runtime boundary."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


class HarnessSessionBusyError(HarnessSessionError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            "session_busy",
            f"Session is already open: {session_id}",
            details={"session_id": session_id},
        )


class HarnessChildSessionRequiresParentError(HarnessSessionError):
    def __init__(self, session_id: str, operation: str) -> None:
        super().__init__(
            "child_session_requires_parent",
            f"A subagent Session cannot be {operation} independently: {session_id}",
            details={
                "reason": "child_session_requires_parent",
                "session_id": session_id,
            },
        )


class HarnessInvalidSessionStoreError(HarnessSessionError):
    def __init__(self, session_id: str, message: str) -> None:
        super().__init__(
            "invalid_session_store",
            message,
            details={"session_id": session_id},
        )


class HarnessStoreRequiresNewerReaderError(HarnessInvalidSessionStoreError):
    """Raised when a store was written by a newer store-format minor.

    A newer writer records a higher store-format minor and may add records or
    fields this reader cannot interpret. The store is intact; it just needs a
    newer reader. This subclasses the invalid-store error so callers that already
    handle an unreadable store keep working, while its distinct code and message
    let a host tell the difference between a store that needs an upgrade and one
    that is broken.
    """

    def __init__(self, session_id: str, store_format_minor: int) -> None:
        HarnessSessionError.__init__(
            self,
            "store_requires_newer_reader",
            f"This session was written by a newer store format (1.{store_format_minor}) "
            "and needs a newer reader to open.",
            details={
                "session_id": session_id,
                "store_format_minor": store_format_minor,
            },
        )


class HarnessUnfinishedMigrationError(HarnessSessionError):
    def __init__(self, session_id: str, source_backend: str) -> None:
        super().__init__(
            "unfinished_work_migration",
            f"The {source_backend} session has unfinished recoverable work: {session_id}",
            details={"session_id": session_id, "source_backend": source_backend},
        )


class HarnessInvalidMigrationSourceError(HarnessSessionError):
    def __init__(self, session_id: str, source_backend: str, message: str) -> None:
        super().__init__(
            "invalid_migration_source",
            message,
            details={"session_id": session_id, "source_backend": source_backend},
        )


class HarnessCommandConflictError(HarnessSessionError):
    def __init__(self, client_command_id: str, reason: str = "reused with different input") -> None:
        super().__init__(
            "client_command_conflict",
            f"Client command ID was {reason}: {client_command_id}",
            details={"client_command_id": client_command_id},
        )


class HarnessTurnConflictError(HarnessSessionError):
    def __init__(self, active_turn_id: str) -> None:
        super().__init__(
            "turn_conflict",
            f"A turn is already running: {active_turn_id}",
            details={"active_turn_id": active_turn_id},
        )


class HarnessTurnQueueFullError(HarnessSessionError):
    def __init__(self, max_items: int) -> None:
        super().__init__(
            "turn_queue_full",
            f"Turn queue is full ({max_items} items)",
            details={"max_items": max_items},
        )


class HarnessTurnQueueIdempotencyConflictError(HarnessSessionError):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(
            "turn_queue_idempotency_conflict",
            f"Idempotency key was already used with different input: {idempotency_key}",
            details={"idempotency_key": idempotency_key},
        )


class HarnessTurnQueueItemNotFoundError(HarnessSessionError):
    def __init__(self, queue_item_id: str) -> None:
        super().__init__(
            "turn_queue_item_not_found",
            f"Queued turn not found: {queue_item_id}",
            details={"queue_item_id": queue_item_id},
        )


class HarnessTurnQueuePendingError(HarnessSessionError):
    def __init__(self) -> None:
        super().__init__(
            "turn_queue_pending",
            "Resume queued turns before starting a direct turn",
        )


class HarnessContextCompactionError(HarnessSessionError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Any = None,
        after_response: Callable[[], None] | None = None,
        on_response_abandoned: Callable[[], None] | None = None,
    ) -> None:
        self.after_response = after_response
        self.on_response_abandoned = on_response_abandoned
        super().__init__(
            "context_compaction_failed",
            message,
            details={"reason": code, "details": details},
        )


class HarnessStaleTurnError(HarnessSessionError):
    def __init__(self, active_turn_id: str | None) -> None:
        super().__init__(
            "stale_turn",
            "No matching active turn",
            details={"active_turn_id": active_turn_id},
        )


class HarnessCallbackClosedError(HarnessSessionError):
    def __init__(self, callback_id: str) -> None:
        super().__init__(
            "callback_closed",
            f"Callback is closed: {callback_id}",
            details={"callback_id": callback_id},
        )


class HarnessCallbackNotFoundError(HarnessSessionError):
    def __init__(self, callback_id: str) -> None:
        super().__init__(
            "callback_not_found",
            f"Callback not found: {callback_id}",
            details={"callback_id": callback_id},
        )


class HarnessCallbackConflictError(HarnessSessionError):
    def __init__(self, callback_id: str) -> None:
        super().__init__(
            "callback_conflict",
            f"Callback result conflicts with the accepted result: {callback_id}",
            details={"callback_id": callback_id},
        )


class HarnessReplayDivergenceError(HarnessSessionError):
    def __init__(self, session_id: str, sequence: int) -> None:
        super().__init__(
            "replay_divergence",
            f"Core replay diverged for session {session_id} at sequence {sequence}",
            details={"session_id": session_id, "sequence": sequence},
        )


class HarnessResourceLockError(HarnessSessionError):
    def __init__(self, session_id: str, resource: str) -> None:
        super().__init__(
            "resource_lock_mismatch",
            f"Stored {resource} lock does not match the current environment",
            details={"session_id": session_id, "resource": resource},
        )


class HarnessNotImplementedError(NotImplementedError):
    """Raised when a Harness operation is unavailable."""


class HarnessSessionNotFoundError(LookupError):
    """Raised when an operation names a session this Host does not hold."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class HarnessSessionDeleteError(HarnessSessionError):
    def __init__(self, session_id: str, message: str) -> None:
        super().__init__(
            "session_delete_failed",
            message,
            details={"reason": "session_delete_failed", "session_id": session_id},
        )


__all__ = [
    "HarnessCallbackClosedError",
    "HarnessCallbackConflictError",
    "HarnessCallbackNotFoundError",
    "HarnessChildSessionRequiresParentError",
    "HarnessCommandConflictError",
    "HarnessContextCompactionError",
    "HarnessInvalidMigrationSourceError",
    "HarnessInvalidSessionStoreError",
    "HarnessNotImplementedError",
    "HarnessReplayDivergenceError",
    "HarnessResourceLockError",
    "HarnessSessionBusyError",
    "HarnessSessionDeleteError",
    "HarnessSessionError",
    "HarnessSessionNotFoundError",
    "HarnessStaleTurnError",
    "HarnessTurnConflictError",
    "HarnessTurnQueueFullError",
    "HarnessTurnQueueIdempotencyConflictError",
    "HarnessTurnQueueItemNotFoundError",
    "HarnessTurnQueuePendingError",
    "HarnessUnfinishedMigrationError",
]
