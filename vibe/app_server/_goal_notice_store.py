"""The goal's transcript notices, kept by the app server beside the session.

The runtime keeps the conversation; the notices that show where the goal changed
or worked are Vibe's own, so they live in ``goal-notices.json`` and join the
history the app server returns. Each notice is placed against the entries of
the turn it names: a continuation's notice heads its turn's output, a stop
follows its turn, and a notice without a turn goes where its time puts it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence, Set
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vibe.app_server.models import (
    GoalNoticeDetail,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
)
from vibe.utils.io import atomic_replace, file_write_lock, read_safe, read_safe_async

__all__ = [
    "GOAL_NOTICES_FILENAME",
    "MAX_GOAL_NOTICES",
    "GoalNoticeStore",
    "goal_notice_entry",
    "read_stored_goal_notices",
    "with_goal_notices",
]

GOAL_NOTICES_FILENAME = "goal-notices.json"
# Enough for any real goal history; the oldest go first past it.
MAX_GOAL_NOTICES = 2000


class _StoredNoticesV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: Literal["vibe.goal-notices/v1"] = "vibe.goal-notices/v1"
    notices: list[PublicNoticeEntry] = Field(default_factory=list)


def read_stored_goal_notices(session_dir: Path) -> tuple[PublicNoticeEntry, ...]:
    """Best-effort read for a session no adapter has open."""
    path = session_dir / GOAL_NOTICES_FILENAME
    try:
        if not path.exists():
            return ()
        raw = read_safe(path, raise_on_error=True).text
        return tuple(_StoredNoticesV1.model_validate_json(raw).notices)
    except Exception:
        return ()


class GoalNoticeStore:
    """The notices of one session, in the order they were recorded."""

    def __init__(self, path: Path, *, persistent: Callable[[], bool]) -> None:
        self._path = path
        self._persistent = persistent
        self._notices: list[PublicNoticeEntry] = []
        self._lock = asyncio.Lock()

    @property
    def notices(self) -> tuple[PublicNoticeEntry, ...]:
        return tuple(self._notices)

    async def restore(self) -> None:
        """Load the saved notices; an unreadable file only loses the notices."""
        if not self._path.exists():
            return
        try:
            raw = (await read_safe_async(self._path, raise_on_error=True)).text
            stored = _StoredNoticesV1.model_validate_json(raw)
        except Exception:
            return
        async with self._lock:
            self._notices = stored.notices

    async def append(self, notice: PublicNoticeEntry) -> None:
        async with self._lock:
            self._notices = [*self._notices, notice][-MAX_GOAL_NOTICES:]
            await self._persist()

    async def keep_before(
        self, created_at: int, *, kept_turns: Set[str], removed_turns: Set[str]
    ) -> None:
        """Drop the notices of the turns a rewind removed.

        A notice of a turn the history keeps stays even when it was recorded
        later: a queued turn can start before the stop of the turn ahead of it
        is recorded. Any other notice stays only if it predates the rewind.
        """
        async with self._lock:
            kept = [
                n
                for n in self._notices
                if n.turn_id in kept_turns
                or (n.turn_id not in removed_turns and n.created_at < created_at)
            ]
            if kept != self._notices:
                self._notices = kept
                await self._persist()

    async def persist(self) -> None:
        async with self._lock:
            await self._persist()

    async def _persist(self) -> None:
        if not self._persistent():
            return
        if not self._notices:
            await asyncio.to_thread(self._path.unlink, missing_ok=True)
            return
        self._path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        # The notices quote the objective, which is session content.
        self._path.touch(mode=0o600, exist_ok=True)
        payload = _StoredNoticesV1(notices=self._notices).model_dump_json(
            by_alias=True, exclude_none=True
        )
        async with file_write_lock(self._path):
            await atomic_replace(self._path, payload + "\n")


def goal_notice_entry(
    *,
    notice_id: str,
    session_id: str,
    turn_id: str | None,
    created_at: int,
    level: Literal["info", "warning", "error"],
    message: str,
    detail: GoalNoticeDetail,
) -> PublicNoticeEntry:
    return PublicNoticeEntry(
        id=notice_id,
        session_id=session_id,
        turn_id=turn_id,
        created_at=created_at,
        updated_at=created_at,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        level=level,
        message=message,
        detail=detail,
    )


def with_goal_notices(
    history: Sequence[PublicHistoryEntry],
    notices: Sequence[PublicNoticeEntry],
    *,
    complete: bool,
) -> list[PublicHistoryEntry]:
    """``history`` with the goal notices that fall within it, in place.

    A window that does not start at the session's first entry takes only the
    notices of the turns it shows, and the notices without a turn recorded
    since its first entry, so paging never repeats one. Its first entry stays
    a history entry: it is the cursor to the older page, which shows any
    notice that belongs above it.
    """
    entries = list(history)
    if not notices:
        return entries
    if not entries:
        return list(notices) if complete else []
    floor = entries[0].created_at
    known = {entry.id for entry in entries}
    shown_turns = {entry.turn_id for entry in entries if entry.turn_id is not None}
    for notice in notices:
        if notice.id in known:
            continue
        if not complete and not _in_window(notice, floor, shown_turns):
            continue
        at = _position(entries, notice)
        if at == 0 and not complete:
            continue
        entries.insert(at, notice)
    return entries


def _in_window(notice: PublicNoticeEntry, floor: int, shown_turns: Set[str]) -> bool:
    if notice.turn_id is not None:
        return notice.turn_id in shown_turns
    return notice.created_at >= floor


def _position(entries: list[PublicHistoryEntry], notice: PublicNoticeEntry) -> int:
    turn = notice.turn_id
    event = notice.detail.event if isinstance(notice.detail, GoalNoticeDetail) else ""
    if turn is not None:
        in_turn = [i for i, entry in enumerate(entries) if entry.turn_id == turn]
        if in_turn and event == "continued":
            output = [i for i in in_turn if not _is_user_message(entries[i])]
            return output[0] if output else in_turn[-1] + 1
        if in_turn:
            return in_turn[-1] + 1
    return next(
        (i for i, entry in enumerate(entries) if entry.created_at > notice.created_at),
        len(entries),
    )


def _is_user_message(entry: PublicHistoryEntry) -> bool:
    return isinstance(entry, PublicMessageEntry) and entry.role == "user"
