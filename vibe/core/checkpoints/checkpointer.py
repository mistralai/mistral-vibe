from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from vibe.core.checkpoints._events import _Decide, _Edit, _Event, _rid_key, _TurnMark
from vibe.core.checkpoints.history import History
from vibe.core.checkpoints.models import (
    AgentTurn,
    Decision,
    FileState,
    FileStateError,
    ManualEdit,
    Owner,
    RegionId,
    TurnStateError,
)


def _as_keep(decision: Decision) -> bool:
    if decision is Decision.PENDING:
        raise FileStateError("A decision must be KEEP or REVERT, not PENDING")
    return decision is Decision.KEEP


@dataclass(slots=True)
class _OpenTurn:
    mark: _TurnMark
    post: dict[str, FileState] = field(default_factory=dict)


class Checkpointer:
    """An append-only log of turns, manual edits and decisions, resolved into
    file states through a pure :class:`History`.

    A single ordered log records everything that happened to the tracked files:
    **turns**, **manual edits** (each its own owner slot) and **decisions**
    (keep/revert). The log never touches disk — callers feed in content and apply
    the content it returns.

    - Truncating the log at a turn boundary (dropping that turn and every later
      event, decisions included) and re-projecting yields the earlier state.
    - Projecting the log as per-hunk changes drives per-hunk decisions. Each hunk
      depends on the hunks it was built on; reverting one drags its dependents, so
      every projection is a dependency-closed drop and never a merge. There is no
      un-revert (the one conflict-prone direction) — resurrect a change by
      truncating the log or with a fresh edit.

    Manual edits are captured by ``reconcile``: at each acting boundary live disk
    is diffed against the projection and any drift is appended as a manual edit.
    The comparison is against the live projection, so it is idempotent and needs
    no side caches.
    """

    def __init__(self) -> None:
        self._events: list[_Event] = []
        self._seq = 0
        self._manual_index = 0
        self._open: _OpenTurn | None = None

    def _bump(self) -> int:
        self._seq += 1
        return self._seq

    def _next_manual_index(self) -> int:
        self._manual_index += 1
        return self._manual_index

    @property
    def has_open_turn(self) -> bool:
        return self._open is not None

    # -- Snapshot log ----------------------------------------------------------

    def begin_turn(self, turn_id: int) -> None:
        if self._open is not None:
            raise TurnStateError("begin_turn called while a turn is still open")
        mark = _TurnMark(seq=self._bump(), turn_id=turn_id)
        self._events.append(mark)
        self._open = _OpenTurn(mark=mark)

    def record_pre_edit(self, path: str, pre: FileState) -> None:
        if self._open is None:
            raise TurnStateError("record_pre_edit requires an open turn")
        # The first touch in a turn owns that turn's pre-edit state.
        if path in self._open.mark.pre:
            return
        # If the file already drifted from the projection since it was last seen,
        # that is a between-turn manual edit. Seal it as a manual edit ordered just
        # before this turn's marker (it happened before the turn began), so a
        # truncation to this turn keeps it and the turn's hunks build on top of it.
        history = History(self._events)
        if path in history.tracked_paths:
            projection = history.project(path, only_kept=False)
            if pre != projection:
                self._insert_local_before_mark(self._open.mark, path, projection, pre)
        self._open.mark.pre[path] = pre

    def _insert_local_before_mark(
        self, mark: _TurnMark, path: str, before: FileState, after: FileState
    ) -> None:
        deps = History(self._events).compute_deps(path, before, after)
        edit = _Edit(
            seq=self._bump(),
            owner=ManualEdit(self._next_manual_index()),
            path=path,
            before=before,
            after=after,
            deps=deps,
        )
        self._events.insert(self._events.index(mark), edit)

    def record_post_edit(self, path: str, post: FileState) -> None:
        if self._open is None:
            raise TurnStateError("record_post_edit requires an open turn")
        self._open.post[path] = post

    def seal_turn(self) -> None:
        if self._open is None:
            return
        turn = self._open
        for path, before in turn.mark.pre.items():
            after = turn.post.get(path, before)
            self._append_edit(AgentTurn(turn.mark.turn_id), path, before, after)
        self._open = None

    def _append_edit(
        self, owner: Owner, path: str, before: FileState, after: FileState
    ) -> None:
        if before == after:
            return
        deps = History(self._events).compute_deps(path, before, after)
        self._events.append(
            _Edit(
                seq=self._bump(),
                owner=owner,
                path=path,
                before=before,
                after=after,
                deps=deps,
            )
        )

    def clear(self) -> None:
        self._events.clear()
        self._seq = 0
        self._manual_index = 0
        self._open = None

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Roll the log back to its prior state if the body raises, so a review
        decision is only durably committed once its disk persistence succeeds.
        """
        events = list(self._events)
        seq, manual_index, open_turn = self._seq, self._manual_index, self._open
        try:
            yield
        except Exception:
            self._events = events
            self._seq = seq
            self._manual_index = manual_index
            self._open = open_turn
            raise

    # -- Log truncation --------------------------------------------------------

    def drop_turns_from(self, turn_id: int) -> None:
        index = History(self._events).event_index_of_turn(turn_id)
        if index is not None:
            self._events = self._events[:index]
            self._open = None

    # -- Manual-edit capture ---------------------------------------------------

    def reconcile(self, path: str, current: FileState) -> None:
        """Append a manual edit when live disk has drifted from the projection.
        Idempotent, and skipped mid-turn (that disk belongs to the turn). Callers
        invoke it at each acting boundary with the true current disk.
        """
        if self._open is not None:
            return
        projection = History(self._events).project(path, only_kept=False)
        if current != projection:
            self._append_edit(
                ManualEdit(self._next_manual_index()), path, projection, current
            )

    # -- Read model ------------------------------------------------------------

    def view(self, current: dict[str, FileState] | None = None) -> History:
        """A :class:`History` over the log for reads. While a turn is open, each
        drifted path in ``current`` is folded in as a provisional edit so the
        in-flight change renders before it is sealed.
        """
        if self._open is None or not current:
            return History(self._events)
        provisional: list[_Event] = []
        for i, (path, cur) in enumerate(current.items()):
            pre = self._open.mark.pre.get(path)
            if pre is None or pre == cur:
                continue
            provisional.append(
                _Edit(
                    seq=self._seq + 1 + i,
                    owner=AgentTurn(self._open.mark.turn_id),
                    path=path,
                    before=pre,
                    after=cur,
                    deps=History(self._events).compute_deps(path, pre, cur),
                )
            )
        return History([*self._events, *provisional])

    # -- Review decisions ------------------------------------------------------

    def decide_region(self, path: str, region_id: RegionId, decision: Decision) -> None:
        self._ensure_reviewable()
        self._decide(path, [region_id], keep=_as_keep(decision))

    def decide_scope(self, path: str, owner: Owner, decision: Decision) -> None:
        self._ensure_reviewable()
        history = History(self._events)
        eff = history.effective(path)
        targets = [
            rid
            for rid, rid_owner in history.owned_hunks(path)
            if rid_owner == owner and eff.get(rid) is Decision.PENDING
        ]
        self._decide(path, targets, keep=_as_keep(decision))

    def decide_file(self, path: str, decision: Decision) -> None:
        self._ensure_reviewable()
        history = History(self._events)
        eff = history.effective(path)
        targets = [
            rid
            for rid, _deps in history.hunk_order(path)
            if eff.get(rid) is Decision.PENDING
        ]
        self._decide(path, targets, keep=_as_keep(decision))

    # -- Private helpers -------------------------------------------------------

    def _ensure_reviewable(self) -> None:
        if self._open is not None:
            raise TurnStateError(
                "Review decisions are not allowed while a turn is in progress"
            )

    def _decide(self, path: str, region_ids: list[RegionId], *, keep: bool) -> None:
        history = History(self._events)
        order = history.hunk_order(path)
        valid = {rid for rid, _d in order}
        for region_id in region_ids:
            if region_id not in valid:
                raise FileStateError(f"Unknown region for {path}: {region_id}")
        eff = history.effective(path)
        if keep:
            # Keeping a hunk pulls in the pending hunks it is built on, so the
            # approved baseline stays a clean splice.
            deps_of = {rid: deps for rid, deps in order}
            to_keep: set[RegionId] = set()
            stack = list(region_ids)
            while stack:
                rid = stack.pop()
                if rid in to_keep or eff.get(rid) is not Decision.PENDING:
                    continue
                to_keep.add(rid)
                stack.extend(deps_of.get(rid, ()))
            for rid in sorted(to_keep, key=_rid_key):
                self._events.append(_Decide(self._bump(), path, rid, keep=True))
        else:
            # Reverting a hunk drags its dependents, but that is derived by
            # closure at read, so only the target is recorded.
            for rid in region_ids:
                if eff.get(rid) is not Decision.REVERT:
                    self._events.append(_Decide(self._bump(), path, rid, keep=False))
