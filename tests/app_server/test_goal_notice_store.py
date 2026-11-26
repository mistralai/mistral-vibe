from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest

from vibe.app_server._goal_notice_store import (
    GOAL_NOTICES_FILENAME,
    GoalNoticeStore,
    goal_notice_entry,
    read_stored_goal_notices,
    with_goal_notices,
)
from vibe.app_server._model import ProtocolModel
from vibe.app_server.models import (
    GoalNoticeDetail,
    GoalNoticeEvent,
    GoalStatus,
    GoalStatusReason,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    TextContentBlock,
)


def _message(
    entry_id: str,
    role: Literal["user", "assistant"],
    turn_id: str | None,
    created_at: int,
) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id,
        session_id="s",
        turn_id=turn_id,
        created_at=created_at,
        updated_at=created_at,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role=role,
        content=[TextContentBlock(text=entry_id)],
    )


def _notice(
    notice_id: str, event: GoalNoticeEvent, turn_id: str | None, created_at: int
) -> PublicNoticeEntry:
    return goal_notice_entry(
        notice_id=notice_id,
        session_id="s",
        turn_id=turn_id,
        created_at=created_at,
        level="info",
        message=event,
        detail=GoalNoticeDetail(event=event, goal_id="g", status=GoalStatus.ACTIVE),
    )


def _ids(history: list[PublicHistoryEntry]) -> list[str]:
    return [entry.id for entry in history]


def test_a_continuation_notice_heads_its_turns_output() -> None:
    """*Prepare*: A user turn, then a continuation turn whose notice came late.
    *Do*: Merge the notice into the history.
    *Assert*: The notice sits right before the continuation's first output.
    """
    # Prepare
    history = [
        _message("u1", "user", "t1", 10),
        _message("a1", "assistant", "t1", 20),
        _message("a2", "assistant", "t2", 30),
    ]
    notice = _notice("n", "continued", "t2", 35)

    # Do
    merged = with_goal_notices(history, [notice], complete=True)

    # Assert
    assert _ids(merged) == ["u1", "a1", "n", "a2"]


def test_a_stop_notice_follows_its_turn_even_after_a_later_turn_started() -> None:
    """*Prepare*: A goal turn, then a queued user turn already under way.
    *Do*: Merge the stop notice of the goal turn, recorded after the user turn.
    *Assert*: The notice sits after the goal turn, before the user's prompt.
    """
    # Prepare
    history = [
        _message("a1", "assistant", "t1", 10),
        _message("u2", "user", "t2", 20),
        _message("a2", "assistant", "t2", 30),
    ]
    notice = _notice("n", "completed", "t1", 40)

    # Do
    merged = with_goal_notices(history, [notice], complete=True)

    # Assert
    assert _ids(merged) == ["a1", "n", "u2", "a2"]


def test_a_notice_without_a_turn_goes_where_its_time_puts_it() -> None:
    """*Prepare*: Two entries, and a user action recorded between them.
    *Do*: Merge the notice.
    *Assert*: It lands between the two entries.
    """
    # Prepare
    history = [_message("a", "assistant", "t1", 10), _message("b", "user", "t2", 30)]

    # Do
    merged = with_goal_notices(history, [_notice("n", "set", None, 20)], complete=True)

    # Assert
    assert _ids(merged) == ["a", "n", "b"]


def test_a_model_set_notice_naming_its_turn_still_goes_where_its_time_puts_it() -> None:
    """*Prepare*: A running turn, and the model's "Goal set" recorded mid-turn,
    which names that turn only in its detail.
    *Do*: Merge the notice.
    *Assert*: It sits between the turn's entries, where its time puts it, not
    after the turn's last entry.
    """
    # Prepare
    history = [
        _message("u1", "user", "t1", 10),
        _message("a1", "assistant", "t1", 20),
        _message("a2", "assistant", "t1", 40),
    ]
    notice = _notice("n", "set", None, 30)
    notice = notice.model_copy(
        update={"detail": notice.detail.model_copy(update={"turn_id": "t1"})}
    )

    # Do
    merged = with_goal_notices(history, [notice], complete=True)

    # Assert
    assert _ids(merged) == ["u1", "a1", "n", "a2"]


def test_a_partial_window_takes_only_the_notices_since_its_first_entry() -> None:
    """*Prepare*: A window that starts mid-history, and an older notice.
    *Do*: Merge both notices.
    *Assert*: Only the notice inside the window shows, so paging never repeats one.
    """
    # Prepare
    history = [_message("b", "assistant", "t2", 30)]
    notices = [_notice("old", "set", None, 5), _notice("new", "paused", None, 40)]

    # Do
    merged = with_goal_notices(history, notices, complete=False)

    # Assert
    assert _ids(merged) == ["b", "new"]


def test_a_partial_window_keeps_the_continuation_notice_of_a_turn_it_shows() -> None:
    """*Prepare*: A window that starts with a user turn, then a continuation
    turn, and that turn's notice, recorded as the turn started.
    *Do*: Merge the notice.
    *Assert*: The notice heads the continuation's output in the window.
    """
    # Prepare
    history = [
        _message("a", "assistant", "t1", 10),
        _message("b", "assistant", "t2", 30),
    ]
    notices = [_notice("continued", "continued", "t2", 25)]

    # Do
    merged = with_goal_notices(history, notices, complete=False)

    # Assert
    assert _ids(merged) == ["a", "continued", "b"]


def test_a_partial_window_never_starts_with_a_notice() -> None:
    """*Prepare*: A window that starts partway into a continuation turn, whose
    notice belongs above the window's first entry.
    *Do*: Merge the notice.
    *Assert*: The window still starts with its first history entry, the cursor
    to the older page, which shows the notice instead.
    """
    # Prepare
    history = [
        _message("b2", "assistant", "t2", 40),
        _message("b3", "assistant", "t2", 50),
    ]
    notices = [_notice("continued", "continued", "t2", 25)]

    # Do
    merged = with_goal_notices(history, notices, complete=False)

    # Assert
    assert _ids(merged) == ["b2", "b3"]


def test_a_partial_window_leaves_out_the_stop_of_a_turn_it_does_not_show() -> None:
    """*Prepare*: A window that starts at a queued user turn, and the stop
    notice of the goal turn before it, recorded after the queued turn started.
    *Do*: Merge the notice into that window and into the older page's window.
    *Assert*: Only the older page shows it, after its own turn.
    """
    # Prepare
    newer = [_message("u2", "user", "t2", 20), _message("a2", "assistant", "t2", 30)]
    older = [_message("a1", "assistant", "t1", 10), *newer]
    notice = _notice("n", "completed", "t1", 40)

    # Do
    newer_merged = with_goal_notices(newer, [notice], complete=False)
    older_merged = with_goal_notices(older, [notice], complete=True)

    # Assert
    assert _ids(newer_merged) == ["u2", "a2"]
    assert _ids(older_merged) == ["a1", "n", "u2", "a2"]


@pytest.mark.asyncio
async def test_notices_persist_and_a_rewind_drops_the_later_ones(
    tmp_path: Path,
) -> None:
    """*Prepare*: A persistent store with two notices.
    *Do*: Keep only those before a rewind point, then read the file back.
    *Assert*: The file holds the earlier notice only.
    """
    # Prepare
    store = GoalNoticeStore(tmp_path / GOAL_NOTICES_FILENAME, persistent=lambda: True)
    await store.append(_notice("a", "set", None, 10))
    await store.append(_notice("b", "continued", "t1", 30))

    # Do
    await store.keep_before(20, kept_turns=set(), removed_turns={"t1"})

    # Assert
    assert [n.id for n in read_stored_goal_notices(tmp_path)] == ["a"]


@pytest.mark.asyncio
async def test_a_rewind_keeps_the_stop_notice_of_a_turn_it_keeps(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal turn t1 whose completion was recorded after a queued
    user turn t2 had started, and t2's own continuation notice.
    *Do*: Rewind in place to t2's prompt.
    *Assert*: t1's completion stays; t2's notice goes.
    """
    # Prepare
    store = GoalNoticeStore(tmp_path / GOAL_NOTICES_FILENAME, persistent=lambda: True)
    await store.append(_notice("set", "set", None, 5))
    await store.append(_notice("t1-done", "completed", "t1", 40))
    await store.append(_notice("t2-paused", "interrupted", "t2", 50))

    # Do: t2's prompt was created at 20.
    await store.keep_before(20, kept_turns={"t1"}, removed_turns={"t2"})

    # Assert
    assert [n.id for n in store.notices] == ["set", "t1-done"]


class _ReleasedGoalNoticeDetail(ProtocolModel):
    """``GoalNoticeDetail`` as a released app server (3.1.x) reads it."""

    kind: Literal["goal"] = "goal"
    event: GoalNoticeEvent
    goal_id: str
    objective: str | None = None
    status: GoalStatus
    status_reason: GoalStatusReason | None = None
    continuation: int | None = None
    tokens_used: int = 0
    token_budget: int | None = None
    time_used_seconds: int = 0
    error: str | None = None


@pytest.mark.asyncio
async def test_the_saved_notices_leave_out_the_turn_a_released_server_cannot_read(
    tmp_path: Path,
) -> None:
    """*Prepare*: A persistent store, and a "Goal set" the model made, naming
    its turn in the detail.
    *Do*: Save it.
    *Assert*: The file has no ``turnId`` and reads with the released server's
    detail, which forbids unknown fields; the live notice keeps its turn.
    """
    # Prepare
    store = GoalNoticeStore(tmp_path / GOAL_NOTICES_FILENAME, persistent=lambda: True)
    notice = _notice("a", "set", None, 10)
    notice = notice.model_copy(
        update={"detail": notice.detail.model_copy(update={"turn_id": "t1"})}
    )

    # Do
    await store.append(notice)

    # Assert
    raw = (tmp_path / GOAL_NOTICES_FILENAME).read_text(encoding="utf-8")
    assert "turnId" not in raw
    saved = json.loads(raw)["notices"]
    assert [_ReleasedGoalNoticeDetail.model_validate(n["detail"]) for n in saved]
    detail = store.notices[0].detail
    assert isinstance(detail, GoalNoticeDetail)
    assert detail.turn_id == "t1"


def test_a_notice_file_with_non_ascii_text_reads_back(tmp_path: Path) -> None:
    """*Prepare*: A notice whose objective is not ASCII, saved as UTF-8.
    *Do*: Read it back without a live store.
    *Assert*: It reads, whatever the locale's codec.
    """
    # Prepare
    notice = _notice("a", "set", None, 10).model_copy(
        update={"message": "Goal set: café ✓ 日本"}
    )
    (tmp_path / GOAL_NOTICES_FILENAME).write_bytes(
        (
            '{"format": "vibe.goal-notices/v1", "notices": ['
            + notice.model_dump_json(by_alias=True, exclude_none=True)
            + "]}"
        ).encode()
    )

    # Do
    notices = read_stored_goal_notices(tmp_path)

    # Assert
    assert [n.message for n in notices] == ["Goal set: café ✓ 日本"]
