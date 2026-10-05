"""Per-turn file snapshots backing rewind restore on the unified backend.

The harness runtime writes files itself and never snapshots them, so the
adapter records disk states at the turn boundaries it observes and reuses the
core :class:`Checkpointer` read model to answer rewind restores. Turns are
anchored on their opening user entry, which is what a rewind names and what
survives a fork (inherited entries lose their turn id there).

Known gaps versus the legacy engine-side recorder, accepted for this backend:

- the begin-turn read of a carried path happens when the adapter translates
  the turn's start, which can lag the runtime's first write; a ``write_file``
  effect's replaced text overrides that read, but an ``edit`` or a shell
  write racing it restores to the already-written content;
- a file first touched by an ``edit`` or a shell command has no recorded
  pre-state, so a rewind across that first touch leaves it as written; it is
  tracked from that turn on. The same holds for a ``write_file`` overwrite the
  runtime could not diff (a binary or large file carries no replaced text);
- snapshots live in memory, so turns from before this process restore
  nothing, and subagent writes are not recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from vibe.app_server._effect_models import (
    FileEditEffectBatchInput,
    FileEditEffectDetail,
    FileEditEffectInput,
    FileWriteEffectDetail,
    FileWriteEffectOutput,
)
from vibe.app_server.models import (
    BlockedSessionStatus,
    CompletedEffectState,
    PublicEffectEntry,
    PublicMessageEntry,
    PublicSessionState,
    RunningSessionStatus,
)
from vibe.core.checkpoints import Checkpointer, FileState, FileStore
from vibe.observability.logging import logger


class RewindFileSnapshots:
    """Tracks per-turn disk states and answers rewind restores by anchor entry."""

    def __init__(
        self, *, cwd: Callable[[], str | None], files: FileStore | None = None
    ) -> None:
        self._cwd = cwd
        self._files = files or FileStore()
        self._checkpointer = Checkpointer()
        self._next_checkpoint = 0
        self._turns: dict[str, int] = {}
        self._anchors: dict[str, int] = {}
        self._seen_effects: set[str] = set()
        self._begun_turn: str | None = None
        self._touched: set[str] = set()
        self._pending_pre: dict[str, FileState] = {}
        self._observed_active: str | None = None
        self._status_known = False

    def observe(self, state: PublicSessionState) -> None:
        """Fold one translated session state into the snapshot log."""
        active = _active_turn(state)
        if active != self._observed_active:
            self._seal_open_turn()
            if active is not None and active not in self._turns:
                # A turn first seen already running (the subscription's first
                # observation, a restored session) is joined mid-flight: a
                # begin-turn read then would capture its own writes.
                clean = self._status_known and self._observed_active is None
                self._begin_turn(active, state, clean=clean)
            self._observed_active = active
        self._status_known = True
        self._record_file_effects(state)

    def restorable_paths(self, anchor: str) -> list[str]:
        """Paths a rewind to ``anchor`` would change on disk."""
        plan = self._plan(anchor)
        return [
            path
            for path, target in plan.items()
            if (current := self._read(path)) is not None and current != target
        ]

    def restore(self, anchor: str) -> tuple[list[str], list[str]]:
        """Restore disk to its state before ``anchor``'s turn.

        Returns (errors, restored paths).
        """
        errors: list[str] = []
        plan: dict[str, FileState] = {}
        for path, target in self._plan(anchor).items():
            # An unreadable path would raise out of the store mid-restore.
            if self._read(path) is None:
                errors.append(f"Failed to read file: {path}")
                continue
            plan[path] = target
        apply_errors, restored = self._files.apply(plan)
        return [*errors, *apply_errors], restored

    def drop_from(self, anchor: str) -> None:
        """Forget the turns a rewind to ``anchor`` truncated."""
        cut = self._anchors.get(anchor)
        if cut is None:
            # Every recorded turn follows an anchor that was never recorded,
            # so the rewind truncated all of them.
            self._reset()
            return
        if self._begun_turn is not None:
            if self._turns[self._begun_turn] >= cut:
                self._close_open_turn()
            else:
                self._seal_open_turn()
        self._checkpointer.drop_turns_from(cut)
        self._turns = {turn: cp for turn, cp in self._turns.items() if cp < cut}
        self._anchors = {entry: cp for entry, cp in self._anchors.items() if cp < cut}

    def forked(self, anchor: str, renamed: dict[str, str]) -> RewindFileSnapshots:
        """The snapshots a fork before ``anchor`` inherits.

        ``renamed`` maps source anchor entry ids to the fork's own entry ids.
        """
        forked = RewindFileSnapshots(cwd=self._cwd, files=self._files)
        forked._next_checkpoint = self._next_checkpoint
        # A fork is cut from a settled session: its first turn starts clean.
        forked._status_known = True
        cut = self._anchors.get(anchor)
        if cut is None:
            return forked
        forked._checkpointer = self._checkpointer.prefix_before(cut)
        forked._anchors = {
            renamed[entry]: cp
            for entry, cp in self._anchors.items()
            if cp < cut and entry in renamed
        }
        return forked

    def _plan(self, anchor: str) -> dict[str, FileState]:
        cut = self._anchors.get(anchor)
        if cut is None:
            return {}
        plan = self._checkpointer.view().restore_plan_to_turn(cut)
        # The open turn's carried reads join its pre-state only at seal.
        for path, state in self._pending_pre.items():
            plan.setdefault(path, state)
        return plan

    def _begin_turn(
        self, turn_id: str, state: PublicSessionState, *, clean: bool
    ) -> None:
        self._next_checkpoint += 1
        checkpoint = self._next_checkpoint
        self._turns[turn_id] = checkpoint
        anchor = _turn_anchor(state, turn_id)
        if anchor is not None:
            self._anchors[anchor] = checkpoint
        carried = self._checkpointer.view().last_turn_paths()
        self._checkpointer.begin_turn(checkpoint)
        self._begun_turn = turn_id
        if not clean:
            return
        # Held back until a write names its exact replaced text (which wins)
        # or the turn seals: this read can lag the turn's first write.
        for path in carried:
            read = self._read(path)
            if read is not None:
                self._pending_pre[path] = read

    def _seal_open_turn(self) -> None:
        if self._begun_turn is None:
            return
        for path, state in self._pending_pre.items():
            self._record_pre_edit(path, state)
        for path in self._checkpointer.view().last_turn_paths():
            post = self._read(path)
            if post is not None:
                self._checkpointer.record_post_edit(path, post)
        self._checkpointer.seal_turn()
        self._close_open_turn()

    def _close_open_turn(self) -> None:
        self._begun_turn = None
        self._touched = set()
        self._pending_pre = {}

    def _reset(self) -> None:
        self._checkpointer = Checkpointer()
        self._turns = {}
        self._anchors = {}
        self._close_open_turn()

    def _record_file_effects(self, state: PublicSessionState) -> None:
        for entry in state.history or []:
            if (
                not isinstance(entry, PublicEffectEntry)
                or entry.id in self._seen_effects
            ):
                continue
            touch = self._file_touch(entry)
            if touch is None:
                continue
            self._seen_effects.add(entry.id)
            path, replaced = touch
            # Inherited fork entries carry no turn id, so an idle session
            # would otherwise match them against the absent open turn.
            if (
                self._begun_turn is None
                or entry.turn_id != self._begun_turn
                or path in self._touched
            ):
                continue
            self._touched.add(path)
            carried = self._pending_pre.pop(path, None)
            pre = replaced if replaced is not None else carried
            if pre is None:
                # First touch without a known pre-state: track it from here.
                pre = self._read(path)
            if pre is not None:
                self._record_pre_edit(path, pre)

    def _file_touch(
        self, entry: PublicEffectEntry
    ) -> tuple[str, FileState | None] | None:
        """A completed write's (path, exact replaced state if known)."""
        state = entry.state
        detail = entry.detail
        if not isinstance(state, CompletedEffectState):
            return None
        if isinstance(detail, FileWriteEffectDetail) and detail.input is not None:
            return self._resolve(detail.input.file_path), _replaced_state(state)
        if isinstance(detail, FileEditEffectDetail) and isinstance(
            detail.input, FileEditEffectInput | FileEditEffectBatchInput
        ):
            return self._resolve(detail.input.file_path), None
        return None

    def _record_pre_edit(self, path: str, state: FileState) -> None:
        try:
            self._checkpointer.record_pre_edit(path, state)
        except Exception:
            logger.warning(
                "Failed to record rewind snapshot for path=%s", path, exc_info=True
            )

    def _read(self, path: str) -> FileState | None:
        try:
            return self._files.read(path)
        except OSError:
            logger.warning(
                "Failed to read path=%s for rewind snapshots", path, exc_info=True
            )
            return None

    def _resolve(self, path: str) -> str:
        cwd = self._cwd() or str(Path.cwd())
        absolute = Path(path) if Path(path).is_absolute() else Path(cwd) / path
        return str(absolute.resolve())


def _active_turn(state: PublicSessionState) -> str | None:
    status = state.session.status
    if isinstance(status, RunningSessionStatus | BlockedSessionStatus):
        return status.active_turn_id
    return None


def _turn_anchor(state: PublicSessionState, turn_id: str) -> str | None:
    """The user entry that opened ``turn_id``: the id a rewind names."""
    return next(
        (
            entry.id
            for entry in state.history or []
            if isinstance(entry, PublicMessageEntry)
            and entry.role == "user"
            and entry.turn_id == turn_id
        ),
        None,
    )


def _replaced_state(state: CompletedEffectState) -> FileState | None:
    """The exact content a write replaced, None when the runtime could not say."""
    try:
        output = FileWriteEffectOutput.model_validate(state.output)
    except ValueError:
        return None
    if not output.file_existed:
        return FileState.absent()
    if output.previous_content is None:
        return None
    return FileState.from_text(output.previous_content)
