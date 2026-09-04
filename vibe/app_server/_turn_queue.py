from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time
from uuid import uuid4

from vibe.app_server.models import (
    TURN_QUEUE_MAX_ITEMS,
    PublicQueuedTurn,
    PublicTurnQueue,
)
from vibe.app_server.protocol import TurnEnqueueParams


class TurnQueueFullError(RuntimeError):
    def __init__(self, max_items: int) -> None:
        self.max_items = max_items
        super().__init__(f"Turn queue is full ({max_items} items)")


class TurnQueueIdempotencyConflictError(RuntimeError):
    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"Idempotency key was already used with different input: {idempotency_key}"
        )


class TurnQueueItemNotFoundError(RuntimeError):
    def __init__(self, queue_item_id: str) -> None:
        self.queue_item_id = queue_item_id
        super().__init__(f"Queued turn not found: {queue_item_id}")


@dataclass(frozen=True, slots=True)
class QueuedTurnRecord:
    params: TurnEnqueueParams
    queued_turn: PublicQueuedTurn


@dataclass(frozen=True, slots=True)
class TurnQueueEnqueueResult:
    record: QueuedTurnRecord
    duplicate: bool


class TurnQueue:
    """Own queued turns and idempotency receipts for one live Session."""

    def __init__(
        self,
        *,
        max_items: int = TURN_QUEUE_MAX_ITEMS,
        item_id_factory: Callable[[], str] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items must be positive")
        self._max_items = max_items
        self._item_id_factory = item_id_factory or _new_item_id
        self._clock_ms = clock_ms or _now_ms
        self._items: list[QueuedTurnRecord] = []
        self._paused = False
        self._idempotency: dict[str, QueuedTurnRecord] = {}

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def items(self) -> tuple[QueuedTurnRecord, ...]:
        return tuple(self._items)

    @property
    def state(self) -> PublicTurnQueue:
        return PublicTurnQueue(
            items=[item.queued_turn.model_copy(deep=True) for item in self._items],
            paused=self._paused,
            max_items=self._max_items,
        )

    def enqueue(
        self,
        params: TurnEnqueueParams,
        *,
        validate: Callable[[TurnEnqueueParams], None] | None = None,
    ) -> TurnQueueEnqueueResult:
        stored_params = params.model_copy(deep=True)
        key = stored_params.idempotency_key
        if key is not None:
            if existing := self._idempotency.get(key):
                if existing.params != stored_params:
                    raise TurnQueueIdempotencyConflictError(key)
                return TurnQueueEnqueueResult(existing, duplicate=True)
        if len(self._items) >= self._max_items:
            raise TurnQueueFullError(self._max_items)
        if validate is not None:
            validate(stored_params)

        queued_turn = PublicQueuedTurn(
            id=self._item_id_factory(),
            created_at=self._clock_ms(),
            entries=[entry.model_copy(deep=True) for entry in stored_params.entries],
        )
        record = QueuedTurnRecord(stored_params, queued_turn)
        self._items.append(record)
        if key is not None:
            self._idempotency[key] = record
        return TurnQueueEnqueueResult(record, duplicate=False)

    def replace(
        self,
        queue_item_id: str,
        params: TurnEnqueueParams,
        *,
        validate: Callable[[TurnEnqueueParams], None] | None = None,
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
            raise TurnQueueItemNotFoundError(queue_item_id)

        key = stored_params.idempotency_key
        if key is not None:
            if existing := self._idempotency.get(key):
                if (
                    existing.params != stored_params
                    or existing.queued_turn.id != queue_item_id
                ):
                    raise TurnQueueIdempotencyConflictError(key)
                return TurnQueueEnqueueResult(existing, duplicate=True)
        if validate is not None:
            validate(stored_params)

        previous = self._items[index]
        queued_turn = previous.queued_turn.model_copy(
            update={
                "entries": [
                    entry.model_copy(deep=True) for entry in stored_params.entries
                ]
            },
            deep=True,
        )
        record = QueuedTurnRecord(stored_params, queued_turn)
        self._items[index] = record
        if key is not None:
            self._idempotency[key] = record
        return TurnQueueEnqueueResult(record, duplicate=False)

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

    def reset(self) -> None:
        self._items.clear()
        self._paused = False
        self._idempotency.clear()

    def rebind_session(self, session_id: str) -> None:
        for record in self._idempotency.values():
            record.params.session_id = session_id

    def _reset_pause_if_empty(self) -> None:
        if not self._items:
            self._paused = False


def _new_item_id() -> str:
    return str(uuid4())


def _now_ms() -> int:
    return time.time_ns() // 1_000_000
