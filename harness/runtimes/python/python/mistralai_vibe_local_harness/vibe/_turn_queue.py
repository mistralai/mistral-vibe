"""Process-local FIFO queue for future Harness Turns."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from mistralai_vibe_local_harness.protocol import RustContentBlock
from mistralai_vibe_local_harness.session_protocol import (
    TURN_QUEUE_MAX_ITEMS,
    QueuedTurn,
    TurnEnqueueParams,
    TurnQueue,
)
from mistralai_vibe_local_harness.vibe._errors import (
    HarnessTurnQueueFullError,
    HarnessTurnQueueIdempotencyConflictError,
    HarnessTurnQueueItemNotFoundError,
)


@dataclass(frozen=True, slots=True)
class PreparedTurnEntry:
    role: Literal["context", "user"]
    content: tuple[RustContentBlock, ...]


@dataclass(frozen=True, slots=True)
class QueuedTurnRecord:
    params: TurnEnqueueParams
    queued_turn: QueuedTurn
    prepared_entries: tuple[PreparedTurnEntry, ...]


@dataclass(frozen=True, slots=True)
class TurnQueueEnqueueResult:
    record: QueuedTurnRecord
    duplicate: bool


@dataclass(frozen=True, slots=True)
class TurnQueueSteerReceipt:
    queue_item_id: str
    turn_id: str


class SessionTurnQueue:
    """Own queued input and process-local idempotency receipts for one Session."""

    def __init__(
        self,
        *,
        max_items: int = TURN_QUEUE_MAX_ITEMS,
        item_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items must be positive")
        self._max_items = max_items
        self._item_id_factory = item_id_factory or _new_item_id
        self._items: list[QueuedTurnRecord] = []
        self._paused = False
        self._idempotency: dict[str, QueuedTurnRecord] = {}
        # Keep successful receipts for the live Session so a retry after a
        # disconnect cannot submit the same queued content twice.
        self._steer_receipts: dict[str, TurnQueueSteerReceipt] = {}

    def __bool__(self) -> bool:
        return bool(self._items)

    @property
    def state(self) -> TurnQueue:
        return TurnQueue(
            items=[record.queued_turn.model_copy(deep=True) for record in self._items],
            paused=self._paused,
            max_items=self._max_items,
        )

    def enqueue(
        self,
        params: TurnEnqueueParams,
        prepared_entries: Sequence[PreparedTurnEntry],
        *,
        created_at: int,
    ) -> TurnQueueEnqueueResult:
        stored_params = params.model_copy(deep=True)
        key = stored_params.idempotency_key
        if key is not None:
            existing = self._idempotency.get(key)
            if existing is not None:
                if existing.params != stored_params:
                    raise HarnessTurnQueueIdempotencyConflictError(key)
                return TurnQueueEnqueueResult(existing, duplicate=True)
        if len(self._items) >= self._max_items:
            raise HarnessTurnQueueFullError(self._max_items)

        stored_entries = tuple(
            PreparedTurnEntry(
                role=entry.role,
                content=tuple(block.model_copy(deep=True) for block in entry.content),
            )
            for entry in prepared_entries
        )
        queued_turn = QueuedTurn(
            id=self._item_id_factory(),
            created_at=created_at,
            entries=[entry.model_copy(deep=True) for entry in stored_params.entries],
        )
        record = QueuedTurnRecord(
            params=stored_params,
            queued_turn=queued_turn,
            prepared_entries=stored_entries,
        )
        self._items.append(record)
        if key is not None:
            self._idempotency[key] = record
        return TurnQueueEnqueueResult(record, duplicate=False)

    def replace(
        self,
        queue_item_id: str,
        params: TurnEnqueueParams,
        prepared_entries: Sequence[PreparedTurnEntry],
    ) -> TurnQueueEnqueueResult:
        stored_params = params.model_copy(deep=True)
        index = next(
            (
                index
                for index, record in enumerate(self._items)
                if record.queued_turn.id == queue_item_id
            ),
            None,
        )
        if index is None:
            raise HarnessTurnQueueItemNotFoundError(queue_item_id)

        key = stored_params.idempotency_key
        if key is not None:
            existing = self._idempotency.get(key)
            if existing is not None:
                if existing.params != stored_params or existing.queued_turn.id != queue_item_id:
                    raise HarnessTurnQueueIdempotencyConflictError(key)
                return TurnQueueEnqueueResult(existing, duplicate=True)

        stored_entries = tuple(
            PreparedTurnEntry(
                role=entry.role,
                content=tuple(block.model_copy(deep=True) for block in entry.content),
            )
            for entry in prepared_entries
        )
        previous = self._items[index]
        queued_turn = previous.queued_turn.model_copy(
            update={"entries": [entry.model_copy(deep=True) for entry in stored_params.entries]},
            deep=True,
        )
        record = QueuedTurnRecord(
            params=stored_params,
            queued_turn=queued_turn,
            prepared_entries=stored_entries,
        )
        self._items[index] = record
        if key is not None:
            self._idempotency[key] = record
        return TurnQueueEnqueueResult(record, duplicate=False)

    def require_record(self, queue_item_id: str) -> QueuedTurnRecord:
        record = next(
            (record for record in self._items if record.queued_turn.id == queue_item_id),
            None,
        )
        if record is None:
            raise HarnessTurnQueueItemNotFoundError(queue_item_id)
        return record

    def steer_receipt(self, queue_item_id: str) -> TurnQueueSteerReceipt | None:
        return self._steer_receipts.get(queue_item_id)

    def retire_steered(
        self,
        record: QueuedTurnRecord,
        *,
        turn_id: str,
    ) -> TurnQueueSteerReceipt:
        queue_item_id = record.queued_turn.id
        index = next(
            (
                index
                for index, current in enumerate(self._items)
                if current.queued_turn.id == queue_item_id
            ),
            None,
        )
        if index is None:
            raise HarnessTurnQueueItemNotFoundError(queue_item_id)
        if self._items[index] is not record:
            raise RuntimeError(f"Queued turn changed before steering completed: {queue_item_id}")

        self._items.pop(index)
        self._reset_pause_if_empty()
        receipt = TurnQueueSteerReceipt(queue_item_id=queue_item_id, turn_id=turn_id)
        self._steer_receipts[queue_item_id] = receipt
        return receipt

    def peek_next(self) -> QueuedTurnRecord | None:
        if self._paused or not self._items:
            return None
        return self._items[0]

    def pop_next(self) -> QueuedTurnRecord | None:
        if self.peek_next() is None:
            return None
        record = self._items.pop(0)
        self._reset_pause_if_empty()
        return record

    def remove(self, queue_item_id: str) -> bool:
        index = next(
            (
                index
                for index, record in enumerate(self._items)
                if record.queued_turn.id == queue_item_id
            ),
            None,
        )
        if index is None:
            return False
        self._items.pop(index)
        self._reset_pause_if_empty()
        return True

    def pause(self) -> bool:
        if not self._items or self._paused:
            return False
        self._paused = True
        return True

    def resume(self) -> bool:
        if not self._paused:
            return False
        self._paused = False
        return True

    def _reset_pause_if_empty(self) -> None:
        if not self._items:
            self._paused = False


def _new_item_id() -> str:
    return str(uuid4())


__all__ = [
    "PreparedTurnEntry",
    "QueuedTurnRecord",
    "SessionTurnQueue",
    "TurnQueueEnqueueResult",
    "TurnQueueSteerReceipt",
]
