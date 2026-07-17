from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from vibe.core.checkpoints import (
    AgentTurn,
    Checkpointer,
    Decision,
    FileState,
    FileStore,
    HunkSide,
    OpaqueChange,
    OpaqueReason,
    Owner,
    RegionId,
    TurnRegion,
    TurnStateError,
)
from vibe.core.utils.io import decode_safe


class ReviewError(Exception):
    """Raised when a review operation fails."""


class ReviewFileStatus(StrEnum):
    MODIFIED = auto()
    CREATED = auto()
    DELETED = auto()
    BINARY_OR_UNDECODABLE = auto()


RegionRef = tuple[int, int]


@dataclass(frozen=True, slots=True)
class TextReviewRegion:
    """A line hunk: its stable id (version_index + ordinal), owning scope (a model
    turn or a manual edit), line span on each side, and current keep/revert
    decision. ``depends_on`` lists the earlier hunks it is built on (by
    ``(version_index, ordinal)``), so the UI can group hunks that touch the same
    lines across scopes into one control.
    """

    version_index: int
    ordinal: int
    owner: Owner
    baseline_start: int
    baseline_line_count: int
    current_start: int
    current_line_count: int
    decision: Decision
    depends_on: tuple[RegionRef, ...]


@dataclass(frozen=True, slots=True)
class OpaqueReviewRegion:
    """A whole-file change for a file that cannot be line-diffed (binary,
    undecodable, or deleted). No line coordinates — reviewed as a unit.
    """

    version_index: int
    ordinal: int
    owner: Owner
    reason: OpaqueReason
    decision: Decision
    depends_on: tuple[RegionRef, ...]


ReviewRegion = TextReviewRegion | OpaqueReviewRegion


@dataclass(frozen=True, slots=True)
class ReviewFile:
    path: str
    status: ReviewFileStatus
    regions: list[ReviewRegion]


@dataclass(frozen=True, slots=True)
class ReviewScopeFile:
    path: str
    status: ReviewFileStatus
    region_count: int


@dataclass(frozen=True, slots=True)
class ReviewScope:
    """One review slot — a model turn or a manual edit — with its still-pending
    changes per file. Slots keep a permanent position in log order (turns and
    manual edits interleaved).
    """

    owner: Owner
    files: list[ReviewScopeFile]


@dataclass(frozen=True, slots=True)
class ReviewState:
    files: list[ReviewFile]
    scopes: list[ReviewScope]


@dataclass(frozen=True, slots=True)
class TurnFileDiff:
    """A single turn's own change to a file, ready to render: the file before the
    turn against how it stood after. ``status`` is the turn's own contribution
    (opaque sides yield empty text, as with whole-file binary diffs).
    """

    status: ReviewFileStatus
    baseline: str
    current: str


@dataclass(frozen=True, slots=True)
class ReviewHunk:
    """A pending hunk located in a rendered diff, so the UI can pin an inline
    accept/revert control: which ``side`` and 0-based ``line``, and the hunks it
    decides together (its target, by ``(version_index, ordinal)``).
    """

    side: HunkSide
    line: int
    regions: tuple[RegionRef, ...]


@dataclass(frozen=True, slots=True)
class RegionTarget:
    path: str
    version_index: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class RegionsTarget:
    """Several hunks in one file decided together — the aggregated control the
    all-turns view renders for one physical change touched across turns.
    """

    path: str
    regions: tuple[RegionRef, ...]


@dataclass(frozen=True, slots=True)
class ScopeTarget:
    owner: Owner


@dataclass(frozen=True, slots=True)
class FileTarget:
    path: str


@dataclass(frozen=True, slots=True)
class ScopeFileTarget:
    """One scope's own changes to a single file — the middle granularity of the
    single-scope view. Reverting cascades to later dependent hunks; approving
    pulls in the earlier hunks it is built on.
    """

    owner: Owner
    path: str


@dataclass(frozen=True, slots=True)
class AllTarget:
    pass


@dataclass(frozen=True, slots=True)
class LastTurnsTarget:
    count: int


ReviewTarget = (
    RegionTarget
    | RegionsTarget
    | ScopeTarget
    | ScopeFileTarget
    | FileTarget
    | AllTarget
    | LastTurnsTarget
)


def _diff_side(state: FileState) -> tuple[str, bool]:
    """Decoded text for one side of a diff plus whether it is opaque
    (binary/undecodable). Absent files decode to empty text, non-opaque.
    """
    if state.data is None:
        return "", False
    if state.is_binary:
        return "", True
    return decode_safe(state.data).text, False


def _to_review_region(tr: TurnRegion) -> ReviewRegion:
    depends_on = tuple((dep.version_index, dep.ordinal) for dep in tr.depends_on)
    if isinstance(tr.change, OpaqueChange):
        return OpaqueReviewRegion(
            version_index=tr.region_id.version_index,
            ordinal=tr.region_id.ordinal,
            owner=tr.owner,
            reason=tr.change.reason,
            decision=tr.decision,
            depends_on=depends_on,
        )
    return TextReviewRegion(
        version_index=tr.region_id.version_index,
        ordinal=tr.region_id.ordinal,
        owner=tr.owner,
        baseline_start=tr.change.baseline_start,
        baseline_line_count=len(tr.change.baseline_lines),
        current_start=tr.change.current_start,
        current_line_count=len(tr.change.current_lines),
        decision=tr.decision,
        depends_on=depends_on,
    )


class ReviewManager:
    """Impure read/mutate shell over the shared Checkpointer for agent-change
    review: reads current disk to project pending changes and persists reverts.
    Holds no session state — only the Checkpointer and the filesystem.
    """

    def __init__(
        self, checkpointer: Checkpointer, files: FileStore | None = None
    ) -> None:
        self._checkpointer = checkpointer
        self._files = files or FileStore()

    def review_state(self) -> ReviewState:
        current = self._current_states()
        # Render is an acting boundary: capture any manual edit made since the
        # last look before projecting the review.
        for path in current:
            self._checkpointer.reconcile(path, current[path])
        history = self._checkpointer.view(current)
        views = {path: history.regions(path) for path in history.tracked_paths}
        files = [
            review_file
            for path, view in views.items()
            if (review_file := self._review_file(path, current[path], view)) is not None
        ]
        return ReviewState(files=files, scopes=self._review_scopes(files))

    def approve_review(self, target: ReviewTarget) -> list[str]:
        """Accept the targeted hunks into the accepted baseline. Disk is left as
        is — approved content already sits there.
        """
        return self._decide(target, Decision.KEEP)

    def revert_review(self, target: ReviewTarget) -> list[str]:
        """Roll the targeted hunks back, persisting the reconstructed file to disk
        immediately (dependent later hunks are dragged down silently).
        """
        return self._decide(target, Decision.REVERT)

    def baseline_text(self, path: str) -> str:
        cur = self._files.read(path)
        baseline = self._checkpointer.view({path: cur}).accepted_baseline(path)
        if baseline.data is None:
            return ""
        return decode_safe(baseline.data).text

    def scope_file_diff(self, path: str, owner: Owner) -> TurnFileDiff:
        """The diff of a single scope's own change to a file: how it stood before
        the scope against how it stood after. A scope that did not touch the file
        yields an empty diff.
        """
        cur = self._files.read(path)
        pair = self._checkpointer.view({path: cur}).scope_pending_diff(path, owner)
        if pair is None:
            return TurnFileDiff(ReviewFileStatus.MODIFIED, "", "")
        baseline, current = pair
        base_text, base_opaque = _diff_side(baseline)
        cur_text, cur_opaque = _diff_side(current)
        if base_opaque or cur_opaque:
            return TurnFileDiff(ReviewFileStatus.BINARY_OR_UNDECODABLE, "", "")
        if baseline.data is None:
            status = ReviewFileStatus.CREATED
        elif current.data is None:
            status = ReviewFileStatus.DELETED
        else:
            status = ReviewFileStatus.MODIFIED
        return TurnFileDiff(status, base_text, cur_text)

    def file_hunks(self, path: str, owner: Owner | None = None) -> list[ReviewHunk]:
        """The pending hunks of a file's rendered diff, anchored for inline
        accept/revert controls. ``owner`` selects the scope diff; omitting it uses
        the whole-file diff. Reconciles first so the whole-file anchors line up
        with the disk content the panel renders.
        """
        current = self._files.read(path)
        self._checkpointer.reconcile(path, current)
        return [
            ReviewHunk(
                side=anchor.side,
                line=anchor.line,
                regions=tuple((r.version_index, r.ordinal) for r in anchor.regions),
            )
            for anchor in self._checkpointer.view({path: current}).pending_hunks(
                path, owner
            )
        ]

    # -- Private helpers -------------------------------------------------------

    def _current_states(self) -> dict[str, FileState]:
        return {
            path: self._files.read(path)
            for path in self._checkpointer.view().tracked_paths
        }

    def _review_scopes(self, files: list[ReviewFile]) -> list[ReviewScope]:
        # Every owner that ever produced a change keeps a stable slot, in log
        # order (turns and manual edits interleaved), whether or not its hunks are
        # still pending. Its position is therefore its permanent place: resolving a
        # sibling scope never renumbers the rest. A fully decided scope stays with
        # no files, and the UI shows "nothing to review" there.
        scopes: list[ReviewScope] = []
        for owner in self._checkpointer.view().scopes:
            scope_files = [
                ReviewScopeFile(path=file.path, status=file.status, region_count=count)
                for file in files
                if (
                    count := sum(
                        1
                        for r in file.regions
                        if r.owner == owner and r.decision is Decision.PENDING
                    )
                )
            ]
            scopes.append(ReviewScope(owner=owner, files=scope_files))
        return scopes

    def _decide(self, target: ReviewTarget, decision: Decision) -> list[str]:
        # Deciding is an acting boundary: seal any manual edit made since the last
        # look before the decision lands, so a manual edit is never clobbered.
        for path in self._checkpointer.view().tracked_paths:
            self._checkpointer.reconcile(path, self._files.read(path))
        # Commit the decision and persist atomically: if a disk write fails, the
        # decision is rolled back so it is never committed against a file that was
        # not actually changed.
        try:
            with self._checkpointer.atomic():
                paths = self._decide_target(target, decision)
                unique = list(dict.fromkeys(paths))
                for path in unique:
                    self._persist(path)
                return unique
        except TurnStateError as exc:
            raise ReviewError(str(exc)) from exc

    def _decide_target(self, target: ReviewTarget, decision: Decision) -> list[str]:
        match target:
            case RegionTarget(path, version_index, ordinal):
                self._checkpointer.decide_region(
                    path, RegionId(version_index, ordinal), decision
                )
                paths = [path]
            case RegionsTarget(path, regions):
                for version_index, ordinal in regions:
                    self._checkpointer.decide_region(
                        path, RegionId(version_index, ordinal), decision
                    )
                paths = [path]
            case FileTarget(path):
                self._checkpointer.decide_file(path, decision)
                paths = [path]
            case ScopeFileTarget(owner, path):
                self._checkpointer.decide_scope(path, owner, decision)
                paths = [path]
            case ScopeTarget(owner):
                paths = self._decide_scope(owner, decision)
            case AllTarget():
                paths = self._paths_with_pending()
                for path in paths:
                    self._checkpointer.decide_file(path, decision)
            case LastTurnsTarget(count):
                paths = self._decide_last_turns(count, decision)
        return paths

    def _decide_scope(self, owner: Owner, decision: Decision) -> list[str]:
        affected: list[str] = []
        for path in self._checkpointer.view().tracked_paths:
            current = self._files.read(path)
            view = self._checkpointer.view({path: current}).regions(path)
            if any(tr.owner == owner for tr in view):
                self._checkpointer.decide_scope(path, owner, decision)
                affected.append(path)
        return affected

    def _decide_last_turns(self, count: int, decision: Decision) -> list[str]:
        if count <= 0:
            return []
        history = self._checkpointer.view()
        affected: list[str] = []
        for turn_id, _pre in history.turns[history.accepted_turn_frontier() :][-count:]:
            affected.extend(self._decide_scope(AgentTurn(turn_id), decision))
        return affected

    def _persist(self, path: str) -> None:
        current = self._files.read(path)
        content = self._checkpointer.view({path: current}).content(path)
        if content != current:
            self._write_state(path, content)

    def _paths_with_pending(self) -> list[str]:
        history = self._checkpointer.view()
        return [
            path
            for path in history.tracked_paths
            if any(d is Decision.PENDING for d in history.effective(path).values())
        ]

    def _write_state(self, path: str, state: FileState) -> None:
        errors, _ = self._files.apply({path: state})
        if errors:
            raise ReviewError(errors[0])

    def _review_file(
        self, path: str, current: FileState, view: tuple[TurnRegion, ...]
    ) -> ReviewFile | None:
        if not view:
            return None
        # Resolved once every change is decided — approved ones live in the
        # accepted baseline, reverted ones are already back on disk.
        if all(tr.decision is not Decision.PENDING for tr in view):
            return None
        return ReviewFile(
            path=path,
            status=self._status(path, current, view),
            regions=[_to_review_region(tr) for tr in view],
        )

    def _status(
        self, path: str, current: FileState, view: tuple[TurnRegion, ...]
    ) -> ReviewFileStatus:
        if current.data is None:
            return ReviewFileStatus.DELETED
        if any(
            isinstance(tr.change, OpaqueChange)
            and tr.change.reason is OpaqueReason.BINARY_OR_UNDECODABLE
            for tr in view
        ):
            return ReviewFileStatus.BINARY_OR_UNDECODABLE
        if self._checkpointer.view().original(path).data is None:
            return ReviewFileStatus.CREATED
        return ReviewFileStatus.MODIFIED
