from __future__ import annotations

import pytest

from vibe.app_server._turn_queue import (
    TurnQueue,
    TurnQueueFullError,
    TurnQueueIdempotencyConflictError,
    TurnQueueItemNotFoundError,
)
from vibe.app_server.protocol import (
    SessionTextContentBlock,
    TurnEnqueueParams,
    TurnUserInputEntry,
)


def _params(key: str, text: str = "next prompt") -> TurnEnqueueParams:
    return TurnEnqueueParams(
        idempotency_key=key,
        session_id="session-1",
        entries=[
            TurnUserInputEntry(
                entry_id=f"user-{key}", content=[SessionTextContentBlock(text=text)]
            )
        ],
    )


def _queue(*ids: str, max_items: int = 32) -> TurnQueue:
    item_ids = iter(ids or ("queue-1", "queue-2", "queue-3"))
    return TurnQueue(
        max_items=max_items, item_id_factory=lambda: next(item_ids), clock_ms=lambda: 10
    )


def test_enqueue_builds_public_state_from_a_private_copy() -> None:
    queue = _queue()
    params = _params("one")

    result = queue.enqueue(params)
    params.entries[0].content.append(SessionTextContentBlock(text="mutated"))
    state = queue.state
    state.items.clear()

    assert not result.duplicate
    assert result.record.queued_turn.id == "queue-1"
    assert result.record.queued_turn.created_at == 10
    assert result.record.queued_turn.entries[0].entry_id == "user-one"
    assert result.record.queued_turn.entries[0].content == [
        SessionTextContentBlock(text="next prompt")
    ]
    assert result.record.params.entries[0].content == [
        SessionTextContentBlock(text="next prompt")
    ]
    assert len(queue) == 1
    assert queue.state.items == [result.record.queued_turn]


def test_pop_next_preserves_fifo_order() -> None:
    queue = _queue("queue-a", "queue-b")
    queue.enqueue(_params("a"))
    queue.enqueue(_params("b"))

    peeked = queue.peek_next()
    first = queue.pop_next()
    second = queue.pop_next()

    assert peeked is first
    assert first is not None and first.queued_turn.id == "queue-a"
    assert second is not None and second.queued_turn.id == "queue-b"
    assert queue.pop_next() is None


def test_duplicate_enqueue_returns_the_original_record() -> None:
    queue = _queue()
    first = queue.enqueue(_params("same"))
    duplicate = queue.enqueue(_params("same"))

    assert duplicate.duplicate
    assert duplicate.record is first.record
    assert len(queue) == 1


def test_enqueue_without_idempotency_key_creates_distinct_items() -> None:
    queue = _queue("queue-a", "queue-b")
    params = TurnEnqueueParams(
        session_id="session-1",
        entries=[
            TurnUserInputEntry(content=[SessionTextContentBlock(text="next prompt")])
        ],
    )

    first = queue.enqueue(params)
    second = queue.enqueue(params)

    assert first.record.queued_turn.id == "queue-a"
    assert second.record.queued_turn.id == "queue-b"
    assert len(queue) == 2


def test_idempotency_key_rejects_different_input() -> None:
    queue = _queue()
    queue.enqueue(_params("same", "first"))

    with pytest.raises(TurnQueueIdempotencyConflictError) as exc_info:
        queue.enqueue(_params("same", "different"))

    assert exc_info.value.idempotency_key == "same"
    assert len(queue) == 1


def test_full_queue_accepts_replay_but_rejects_new_input() -> None:
    queue = _queue(max_items=1)
    queue.enqueue(_params("one"))

    assert queue.enqueue(_params("one")).duplicate
    with pytest.raises(TurnQueueFullError) as exc_info:
        queue.enqueue(_params("two"))

    assert exc_info.value.max_items == 1
    assert len(queue) == 1


def test_enqueue_validation_failure_does_not_accept_or_reserve_input() -> None:
    queue = _queue("queue-a")
    params = _params("one")

    def reject(_params: TurnEnqueueParams) -> None:
        raise ValueError("invalid input")

    with pytest.raises(ValueError, match="invalid input"):
        queue.enqueue(params, validate=reject)

    accepted = queue.enqueue(params)
    replayed = queue.enqueue(params, validate=reject)

    assert accepted.record.queued_turn.id == "queue-a"
    assert replayed.duplicate
    assert replayed.record is accepted.record
    assert len(queue) == 1


def test_replace_preserves_queue_identity_position_and_idempotency_receipts() -> None:
    queue = _queue("queue-a", "queue-b")
    original = queue.enqueue(_params("a", "A"))
    queue.enqueue(_params("b", "B"))
    replacement_params = TurnEnqueueParams(
        idempotency_key="edit-a",
        session_id="session-1",
        entries=[
            TurnUserInputEntry(
                entry_id=original.record.params.message_entry_id,
                content=[SessionTextContentBlock(text="A-edited")],
            )
        ],
    )

    replaced = queue.replace("queue-a", replacement_params)
    replayed_original = queue.enqueue(_params("a", "A"))
    replayed_edit = queue.replace("queue-a", replacement_params)

    assert not replaced.duplicate
    assert [item.id for item in queue.state.items] == ["queue-a", "queue-b"]
    assert queue.state.items[0].created_at == 10
    assert queue.state.items[0].entries[0].content == [
        SessionTextContentBlock(text="A-edited")
    ]
    assert replayed_original.duplicate
    assert replayed_original.record.queued_turn.id == "queue-a"
    assert replayed_edit.duplicate
    assert replayed_edit.record is replaced.record


def test_replace_replay_rejects_a_queue_item_that_already_started() -> None:
    """*Prepare*: A queued turn is edited once, then promoted out of the queue.
    *Do*: Replay the accepted edit with the same idempotency key.
    *Assert*: The retired queue item is reported as missing instead of replayed.
    """
    # Prepare
    queue = _queue("queue-a")
    original = queue.enqueue(_params("a", "A"))
    replacement_params = TurnEnqueueParams(
        idempotency_key="edit-a",
        session_id="session-1",
        entries=[
            TurnUserInputEntry(
                entry_id=original.record.params.message_entry_id,
                content=[SessionTextContentBlock(text="A-edited")],
            )
        ],
    )
    queue.replace("queue-a", replacement_params)
    queue.pop_next()

    # Do
    with pytest.raises(TurnQueueItemNotFoundError) as exc_info:
        queue.replace("queue-a", replacement_params)

    # Assert
    assert exc_info.value.queue_item_id == "queue-a"


def test_replace_replay_rejects_a_removed_queue_item() -> None:
    """*Prepare*: A queued turn is edited once, then explicitly removed.
    *Do*: Replay the accepted edit with the same idempotency key.
    *Assert*: The retired queue item is reported as missing instead of replayed.
    """
    # Prepare
    queue = _queue("queue-a")
    original = queue.enqueue(_params("a", "A"))
    replacement_params = TurnEnqueueParams(
        idempotency_key="edit-a",
        session_id="session-1",
        entries=[
            TurnUserInputEntry(
                entry_id=original.record.params.message_entry_id,
                content=[SessionTextContentBlock(text="A-edited")],
            )
        ],
    )
    queue.replace("queue-a", replacement_params)
    queue.remove("queue-a")

    # Do
    with pytest.raises(TurnQueueItemNotFoundError) as exc_info:
        queue.replace("queue-a", replacement_params)

    # Assert
    assert exc_info.value.queue_item_id == "queue-a"


def test_remove_is_idempotent_and_clears_pause_when_empty() -> None:
    queue = _queue("queue-a", "queue-b")
    queue.enqueue(_params("a"))
    queue.enqueue(_params("b"))
    assert queue.pause()

    assert not queue.remove("missing")
    assert queue.remove("queue-b")
    assert queue.paused
    assert queue.remove("queue-a")
    assert not queue.paused
    assert not queue.remove("queue-a")


def test_pause_and_resume_are_idempotent() -> None:
    queue = _queue()

    assert not queue.pause()
    queue.enqueue(_params("one"))
    assert queue.pause()
    assert not queue.pause()
    assert queue.pop_next() is None
    assert queue.resume()
    assert not queue.resume()
    assert queue.pop_next() is not None


def test_retired_idempotency_key_replays_without_requeuing() -> None:
    queue = _queue()
    first = queue.enqueue(_params("one"))
    queue.pop_next()

    replay = queue.enqueue(_params("one"))

    assert replay.duplicate
    assert replay.record is first.record
    assert not queue


def test_retired_idempotency_key_survives_many_later_items() -> None:
    """*Prepare*: An enqueue receipt retired before 256 later keyed items.
    *Do*: Replay the original enqueue request in the same live queue.
    *Assert*: The original receipt is returned without adding another item.
    """
    # Prepare
    queue = _queue(*(f"queue-{index}" for index in range(258)))
    original = queue.enqueue(_params("original"))
    queue.pop_next()
    for index in range(256):
        queue.enqueue(_params(f"later-{index}"))
        queue.pop_next()

    # Do
    replay = queue.enqueue(_params("original"))

    # Assert
    assert replay.duplicate
    assert replay.record is original.record
    assert not queue


def test_reset_clears_queue_pause_and_idempotency() -> None:
    queue = _queue("queue-a", "queue-b")
    queue.enqueue(_params("same"))
    queue.pause()

    queue.reset()
    result = queue.enqueue(_params("same"))

    assert not result.duplicate
    assert result.record.queued_turn.id == "queue-b"
    assert not queue.paused


def test_rebind_session_updates_active_and_retired_records() -> None:
    queue = _queue("queue-a", "queue-b")
    retired = queue.enqueue(_params("retired"))
    queue.pop_next()
    active = queue.enqueue(_params("active"))

    queue.rebind_session("session-2")

    assert active.record.params.session_id == "session-2"
    replay = queue.enqueue(
        _params("retired").model_copy(update={"session_id": "session-2"})
    )
    assert replay.duplicate
    assert replay.record is retired.record
