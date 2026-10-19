"""Per-turn file snapshots backing rewind restore on the unified backend.

The harness runtime writes files itself and never snapshots them, so the
adapter records disk states at the turn boundaries it observes and reuses the
core :class:`Checkpointer` read model to answer rewind restores. Turns are
anchored on their opening user entry, which is what a rewind names and what
survives a fork (inherited entries lose their turn id there).

Known gaps versus the legacy engine-side recorder, accepted for this backend:

- the begin-turn read of a carried path happens when the adapter translates
  the turn's start, which can lag the runtime's first write; the pre-edit
  text a ``write_file`` or ``edit`` effect carried overrides that read, and
  an ``edit`` effect's own old/new text reconstructs the start content when
  the harness could not carry it, but a shell write racing it still restores
  to the already-written content;
- a file first touched by a shell command has no recorded pre-state, so a
  rewind across that first touch leaves it as written; it is tracked from
  that turn on. The same holds for a ``write_file`` overwrite the runtime
  could not diff (a binary or large file carries no replaced text);
- snapshots live in memory, so turns from before this process restore
  nothing, and subagent writes are not recorded;
- a session whose tools run in a sandbox records nothing: its files are not
  on this disk (:class:`UnavailableRewindFileSnapshots`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from vibe.app_server._effect_models import (
    FileEditEffectBatchInput,
    FileEditEffectDetail,
    FileEditEffectInput,
    FileEditEffectOutput,
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
from vibe.core.review import ReviewManager
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
        # The turn's edit effects per path, in observation order: the hunks
        # and exact states a pre-state reconstruction reverses.
        self._turn_edits: dict[str, list[_TurnEffectRecord]] = {}
        # Paths whose pre is a reconstruction later effects can refine; an
        # exact pre (carried read, a write's replaced text) is never overwritten.
        self._reconstructed: set[str] = set()
        self._observed_active: str | None = None
        self._status_known = False

    def observe(self, state: PublicSessionState) -> None:
        """Fold one translated session state into the snapshot log."""
        active = _active_turn(state)
        if active != self._observed_active:
            # A terminal snapshot can carry the last completed file effects.
            # Fold them into the departing turn before sealing its disk state.
            if self._begun_turn is not None:
                self._record_file_effects(state)
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

    def review_manager(self) -> ReviewManager:
        """The review shell over this recorder's checkpoint history.

        Shares the checkpointer and file store with rewind, and carries the
        held-back turn-start reads in as pre states so a command's write — which
        records no file effect — shows in mid-turn reads before the turn seals.
        """
        return ReviewManager(
            self._checkpointer,
            files=self._files,
            carried_pre=lambda: dict(self._pending_pre),
        )

    def review_cache_key(self) -> object:
        """The identity of the checkpoint history a review manager reads.

        A rewind reset replaces the checkpointer, so a review shell cached
        on this key never serves discarded checkpoints.
        """
        # Live reads seal a fresh copy, so a cached handler cannot retain it.
        return object() if self._begun_turn is not None else self._checkpointer

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
        self._seal_checkpoint(self._checkpointer)
        self._close_open_turn()

    def _seal_checkpoint(self, checkpointer: Checkpointer) -> None:
        for path, state in self._pending_pre.items():
            checkpointer.record_pre_edit(path, state)
        for path in checkpointer.view().last_turn_paths():
            post = self._read(path)
            if post is not None:
                checkpointer.record_post_edit(path, post)
        checkpointer.seal_turn()

    def _close_open_turn(self) -> None:
        self._begun_turn = None
        self._touched = set()
        self._pending_pre = {}
        self._turn_edits = {}
        self._reconstructed = set()

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
            if self._begun_turn is None or entry.turn_id != self._begun_turn:
                continue
            appended = False
            if isinstance(entry.detail, FileEditEffectDetail) and replaced is None:
                # Every edit joins the log: the pre state converges to the
                # turn's start content once the turn's effects are observed.
                assert isinstance(entry.state, CompletedEffectState)
                occurrences = _edit_occurrences(entry.state)
                if occurrences:
                    self._turn_edits.setdefault(path, []).append(("edit", occurrences))
                    appended = True
            elif replaced is not None:
                # Exact pre-effect content: a better reversal base than the
                # disk read, so it joins the log even when the path's pre is
                # already recorded.
                self._turn_edits.setdefault(path, []).append(("base", replaced))
                appended = True
            if path in self._touched:
                # A reconstruction-backed pre is refined only by an effect
                # that extended the log: re-reading the disk after an effect
                # that recorded nothing (a write the runtime could not diff)
                # would reverse earlier edits against post-write content and
                # clobber the already-correct snapshot.
                if appended and path in self._reconstructed:
                    pre, exact = self._reconstructed_pre(path)
                    if pre is not None:
                        self._replace_pre_edit(path, pre)
                        if exact:
                            self._reconstructed.discard(path)
                continue
            self._touched.add(path)
            carried = self._pending_pre.pop(path, None)
            if replaced is not None:
                pre = replaced
            elif carried is not None:
                pre = carried
            else:
                # First touch without an exact state: reconstruct the
                # turn-start content from the current disk read.
                pre, _ = self._reconstructed_pre(path)
                self._reconstructed.add(path)
            if pre is not None:
                self._record_pre_edit(path, pre)

    def _file_touch(
        self, entry: PublicEffectEntry
    ) -> tuple[str, FileState | None] | None:
        """A completed file effect's (path, exact replaced state if known)."""
        state = entry.state
        detail = entry.detail
        if not isinstance(state, CompletedEffectState):
            return None
        if isinstance(detail, FileWriteEffectDetail) and detail.input is not None:
            return self._resolve(detail.input.file_path), _replaced_state(state)
        if isinstance(detail, FileEditEffectDetail) and isinstance(
            detail.input, FileEditEffectInput | FileEditEffectBatchInput
        ):
            return self._resolve(detail.input.file_path), _edit_replaced_state(state)
        return None

    def _record_pre_edit(self, path: str, state: FileState) -> None:
        try:
            self._checkpointer.record_pre_edit(path, state)
        except Exception:
            logger.warning(
                "Failed to record rewind snapshot for path=%s", path, exc_info=True
            )

    def _replace_pre_edit(self, path: str, state: FileState) -> None:
        try:
            self._checkpointer.replace_open_pre_edit(path, state)
        except Exception:
            logger.warning(
                "Failed to refine rewind snapshot for path=%s", path, exc_info=True
            )

    def _reconstructed_pre(self, path: str) -> tuple[FileState | None, bool]:
        """The path's turn-start state and whether it is exact.

        The harness runtime writes files before their effects are observable
        here, so a raw disk read at effect time yields post-edit content. Walk
        the turn's effects backward from the current read: undo each edit's
        line-anchored hunks (a hunk that does not sit at its computed position
        is skipped, leaving that part stale rather than corrupt), and on a
        carried pre-edit state (a write's replaced text, an edit's carried
        previous content — exact at that point) restart from it — every effect
        after it is already undone or superseded, so the remaining walk undoes
        only what preceded it. The result is exact once any carried state has
        been used, provisional otherwise (refined as more effects are
        observed). Binary or undecodable files keep the raw read.
        """
        current = self._read(path)
        if current is None or current.data is None:
            return current, True
        records = self._turn_edits.get(path)
        if not records:
            return current, True
        try:
            lines = current.data.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            logger.debug(
                "Rewind pre-state for path=%s is not UTF-8; keeping the raw read", path
            )
            return current, True
        exact = False
        for record in reversed(records):
            match record:
                case ("edit", occurrences):
                    lines = _undo_occurrences(lines, occurrences)
                case ("base", base):
                    if base.data is None:
                        return FileState.absent(), True
                    try:
                        lines = base.data.decode("utf-8").splitlines(keepends=True)
                        exact = True
                    except UnicodeDecodeError:
                        logger.debug(
                            "Carried pre-state for path=%s is not UTF-8; "
                            "keeping the raw read",
                            path,
                        )
                        return current, True
        return FileState.from_text("".join(lines)), exact

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


class UnavailableRewindFileSnapshots:
    """Rewind file snapshots for a session whose files are not on this disk.

    Records nothing, so a rewind restores no file: reading each written path
    back would cost a sandbox call per write, and the host disk holds
    different files, or none, at those paths.
    """

    def observe(self, state: PublicSessionState) -> None:
        """Ignore the state: nothing is recorded."""

    def restorable_paths(self, anchor: str) -> list[str]:
        """No path: nothing was recorded."""
        return []

    def restore(self, anchor: str) -> tuple[list[str], list[str]]:
        """Restore nothing and report no error."""
        return [], []

    def drop_from(self, anchor: str) -> None:
        """Nothing to forget."""

    def forked(
        self, anchor: str, renamed: dict[str, str]
    ) -> UnavailableRewindFileSnapshots:
        """A fork's files are no more on this disk than its source's."""
        return self

    def review_manager(self) -> ReviewManager:
        """Review over no checkpoint history: nothing was recorded."""
        return ReviewManager(Checkpointer())

    def review_cache_key(self) -> object:
        """A constant: nothing is ever recorded, so the history never changes."""
        return self


type SessionRewindFileSnapshots = RewindFileSnapshots | UnavailableRewindFileSnapshots


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


def _edit_replaced_state(state: CompletedEffectState) -> FileState | None:
    """The exact content an edit replaced, None when the harness did not carry it."""
    try:
        output = FileEditEffectOutput.model_validate(state.output)
    except ValueError:
        return None
    if output.previous_content is None:
        return None
    return FileState.from_text(output.previous_content)


# One observed file effect of the open turn: an edit's line-anchored hunks,
# or a write's exact replaced state (a reversal base).
_TurnEffectRecord = (
    tuple[Literal["edit"], list[tuple[int, int | None, str, str]]]
    | tuple[Literal["base"], FileState]
)


def _edit_occurrences(
    state: CompletedEffectState,
) -> list[tuple[int, int | None, str, str]]:
    """An edit effect's line-anchored hunks.

    Each hunk is ``(start_line, new_start_line, old, new)``: the harness
    computes ``start_line`` on the file after the effect's earlier blocks
    were applied, and ``new_start_line`` where the replaced text then sat —
    both already shifted by those blocks, never by the later ones. An effect
    without positioned hunks cannot be undone positionally, so it
    contributes nothing (a raw string reversal is not a true inverse).
    """
    try:
        output = FileEditEffectOutput.model_validate(state.output)
    except ValueError:
        return []
    occurrences = []
    for occurrence in output.occurrences:
        if occurrence.start_line is None:
            return []
        occurrences.append((
            occurrence.start_line,
            occurrence.new_start_line,
            occurrence.old_text,
            occurrence.new_text,
        ))
    return occurrences


def _undo_occurrences(
    lines: list[str], occurrences: list[tuple[int, int | None, str, str]]
) -> list[str]:
    """Undo one effect's hunks on its post-effect lines.

    A hunk's anchor (``new_start_line`` when the effect carried it, else
    ``start_line``) is where its new text sits after that hunk and the
    earlier ones, never the later ones. Splices run last-to-first, so
    undoing a later hunk restores the file an earlier anchor describes;
    adding those later hunks' line deltas would point the splice elsewhere.
    A hunk is spliced only where its new text sits line-aligned at the
    anchor: annotations carry the matched substring, which starts mid-line
    for in-line edits, and a wrong splice would corrupt the reconstruction
    where a skipped hunk merely leaves it stale. A deletion hunk (empty new
    text) leaves nothing at its position to verify against, so it is always
    skipped for the same reason. An anchor outside the file (a malformed hunk
    naming a line before the start or past the end) is skipped the same way:
    a negative position would slice from the end and splice there. A BOM
    file's first line is compared and spliced with the BOM character set
    aside: the harness reads it as utf-8-sig, so hunk text never carries it
    where the disk read keeps it.
    """
    positions: list[tuple[int, str, str]] = []
    for start_line, new_start_line, old_text, new_text in reversed(occurrences):
        anchor = new_start_line if new_start_line is not None else start_line
        positions.append((anchor - 1, old_text, new_text))
    result = lines
    for position, old_text, new_text in positions:
        new_lines = new_text.splitlines()
        if not new_lines:
            continue
        if position < 0 or position + len(new_lines) > len(result):
            continue
        block = result[position : position + len(new_lines)]
        compared = [line.rstrip("\r\n") for line in block]
        if position == 0 and compared and compared[0].startswith("\ufeff"):
            compared[0] = compared[0].removeprefix("\ufeff")
        if compared != new_lines:
            continue
        spliced = _resurrect_lines(old_text, block)
        if (
            position == 0
            and block[0].startswith("\ufeff")
            and spliced
            and not spliced[0].startswith("\ufeff")
        ):
            spliced[0] = "\ufeff" + spliced[0]
        result[position : position + len(new_lines)] = spliced
    return result


def _resurrect_lines(old_text: str, block: list[str]) -> list[str]:
    """The replaced hunk's lines, with faithful line endings.

    Endings the old text carries itself are authoritative; a hunk without a
    trailing newline (a matched substring that ended mid-line) takes each
    missing ending from the block line it replaces, and an expansion takes
    the block's last ending (an EOF line without a newline stays that way).
    """
    if not old_text:
        return []
    rebuilt = old_text.splitlines(keepends=True)
    endings = [_line_ending(line) for line in block]
    for index, line in enumerate(rebuilt):
        if _line_ending(line):
            continue
        ending = (
            endings[index] if index < len(endings) else endings[-1] if endings else "\n"
        )
        rebuilt[index] = line + ending
    return rebuilt


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith(("\n", "\r")):
        return line[-1]
    return ""
