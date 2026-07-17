from __future__ import annotations

from collections.abc import Iterator
from difflib import SequenceMatcher

from vibe.core.checkpoints._events import _Decide, _Edit, _Event, _rid_key, _TurnMark
from vibe.core.checkpoints.models import (
    AgentTurn,
    Decision,
    FileState,
    HunkAnchor,
    HunkSide,
    OpaqueChange,
    OpaqueReason,
    Owner,
    Region,
    RegionId,
    TurnRegion,
)
from vibe.core.utils.io import decode_safe, encode_safe

# -- Text / line helpers ------------------------------------------------------


def _decode_lines(state: FileState) -> list[str] | None:
    """Keepends lines for text, None for binary, [] for an absent file."""
    if state.data is None:
        return []
    if state.is_binary:
        return None
    return decode_safe(state.data).text.splitlines(keepends=True)


def _lines(state: FileState) -> list[str]:
    return _decode_lines(state) or []


def _reencode(text: str, ref: FileState) -> FileState:
    """Re-encode reconstructed ``text`` preserving ``ref``'s encoding and newline
    style, so a partial approve/revert of a CRLF, cp1252 or UTF-16 file does not
    silently rewrite unrelated bytes to UTF-8/LF. ``ref`` is the domain's choice
    of which file convention to keep; the byte mechanics live in ``encode_safe``.
    """
    if ref.data is None:
        return FileState.from_text(text)
    decoded = decode_safe(ref.data)
    return FileState(
        encode_safe(text, encoding=decoded.encoding, newline=decoded.newline)
    )


def _regions(base_lines: list[str], cur_lines: list[str]) -> Iterator[Region]:
    matcher = SequenceMatcher(a=base_lines, b=cur_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        yield Region(
            baseline_start=i1,
            baseline_lines=tuple(base_lines[i1:i2]),
            current_start=j1,
            current_lines=tuple(cur_lines[j1:j2]),
        )


def _base_to_result(base: list[str], result: list[str]) -> list[int]:
    mapping = [0] * (len(base) + 1)
    for tag, i1, i2, j1, j2 in SequenceMatcher(
        a=base, b=result, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1 + 1):
                mapping[i1 + offset] = j1 + offset
        else:
            mapping[i1] = j1
            mapping[i2] = j2
    return mapping


def _splice(result: list[str], base: list[str], hunks: list[Region]) -> None:
    """Apply ``hunks`` (expressed against ``base``) onto ``result`` in place,
    rebasing each hunk's span through ``base -> result`` and splicing right to
    left so earlier edits do not shift later ones. Dependency closure guarantees
    the touched lines are present in ``result``, so each splice lands exactly.
    """
    mapping = _base_to_result(base, result)
    edits = sorted(
        (
            mapping[h.baseline_start],
            mapping[h.baseline_start + len(h.baseline_lines)],
            h.current_lines,
        )
        for h in hunks
    )
    for start, end, lines in reversed(edits):
        result[start:end] = lines


def _splice_with_prov(
    result: list[str],
    prov: list[RegionId | None],
    base: list[str],
    hunks: list[tuple[RegionId, Region]],
) -> None:
    mapping = _base_to_result(base, result)
    items = sorted(
        (
            mapping[h.baseline_start],
            mapping[h.baseline_start + len(h.baseline_lines)],
            h.current_lines,
            rid,
        )
        for rid, h in hunks
    )
    for start, end, lines, rid in reversed(items):
        result[start:end] = list(lines)
        prov[start:end] = [rid] * len(lines)


def _match_deletions(
    removed: list[str],
    deletions: list[tuple[RegionId, tuple[str, ...]]],
    consumed: set[RegionId],
) -> set[RegionId]:
    """Attribute a rendered delete block to the pending deletions that produced
    it, claiming each at a distinct, non-overlapping run of its removed lines and
    only once across blocks. This keeps two independent deletions of identical
    text from being decided together.
    """
    used = [False] * len(removed)
    seeds: set[RegionId] = set()
    for rid, content in deletions:
        if rid in consumed or not content:
            continue
        n = len(content)
        for i in range(len(removed) - n + 1):
            if any(used[i : i + n]) or tuple(removed[i : i + n]) != content:
                continue
            seeds.add(rid)
            consumed.add(rid)
            for k in range(i, i + n):
                used[k] = True
            break
    return seeds


def _opaque_reason(before: FileState, after: FileState) -> OpaqueReason:
    if before.data is None or after.data is None:
        return OpaqueReason.MISSING
    return OpaqueReason.BINARY_OR_UNDECODABLE


# -- Edit helpers over the event types ----------------------------------------


def _is_opaque(edit: _Edit) -> bool:
    lb = _decode_lines(edit.before)
    la = _decode_lines(edit.after)
    if lb is None or la is None or edit.after.data is None:
        return True
    # An existence toggle with no textual difference (e.g. creating an empty
    # file) has no line hunk to review, so treat it as a whole-file change.
    return edit.before.exists != edit.after.exists and lb == la


def _compute_changes(edit: _Edit) -> list[tuple[int, Region | OpaqueChange]]:
    """The hunks of one edit: line hunks for text, or one whole-file unit if opaque."""
    if _is_opaque(edit):
        reason = _opaque_reason(edit.before, edit.after)
        return [(0, OpaqueChange(reason, edit.before, edit.after))]
    return list(enumerate(_regions(_lines(edit.before), _lines(edit.after))))


def _reference_state(edits: list[_Edit]) -> FileState:
    """A present file state to source encoding/newline from when re-encoding a
    reconstruction: the earliest concrete ``before``, else the earliest ``after``.
    """
    for edit in edits:
        if edit.before.data is not None:
            return edit.before
    for edit in edits:
        if edit.after.data is not None:
            return edit.after
    return FileState.absent()


class History:
    """The read model: pure resolution over an immutable event list.

    Turns a log of turns, manual edits and decisions into file states, per-hunk
    views, hunk anchors, restore plans and log summaries. It never mutates the log
    and is agnostic to how the result is produced or consumed. The Checkpointer
    hands one out through ``view(current)``.
    """

    __slots__ = ("_changes_cache", "_edits_cache", "_effective_cache", "_events")

    def __init__(self, events: list[_Event]) -> None:
        # Copy so the memo caches can assume a frozen event list, even if the
        # caller keeps mutating the list it was handed (the Checkpointer does).
        self._events = list(events)
        self._edits_cache: dict[str, list[_Edit]] = {}
        self._changes_cache: dict[int, list[tuple[int, Region | OpaqueChange]]] = {}
        self._effective_cache: dict[str, dict[RegionId, Decision]] = {}

    def _changes_of(self, edit: _Edit) -> list[tuple[int, Region | OpaqueChange]]:
        cached = self._changes_cache.get(edit.seq)
        if cached is None:
            cached = _compute_changes(edit)
            self._changes_cache[edit.seq] = cached
        return cached

    def has_edits(self, path: str) -> bool:
        return bool(self._edits_for(path))

    def project(self, path: str, *, only_kept: bool) -> FileState:
        return self._reconstruct(path, self._applied(path, only_kept=only_kept))

    def content(self, path: str) -> FileState:
        return self.project(path, only_kept=False)

    def accepted_baseline(self, path: str) -> FileState:
        return self.project(path, only_kept=True)

    def is_fully_reviewed(self, path: str) -> bool:
        return all(
            decision is not Decision.PENDING
            for decision in self.effective(path).values()
        )

    def regions(self, path: str) -> tuple[TurnRegion, ...]:
        eff = self.effective(path)
        out: list[TurnRegion] = []
        for edit in self._edits_for(path):
            for ordinal, change in self._changes_of(edit):
                rid = RegionId(edit.seq, ordinal)
                out.append(
                    TurnRegion(
                        region_id=rid,
                        owner=edit.owner,
                        change=change,
                        decision=eff[rid],
                        depends_on=edit.deps.get(ordinal, ()),
                    )
                )
        return tuple(out)

    def effective(self, path: str) -> dict[RegionId, Decision]:
        """Each hunk's decision after dragging: REVERT if reverted explicitly or
        if any hunk it depends on is.
        """
        cached = self._effective_cache.get(path)
        if cached is not None:
            return cached
        explicit = self._explicit_decisions(path)
        order = self.hunk_order(path)
        reverted: set[RegionId] = set()
        for rid, deps in order:
            if explicit.get(rid) is Decision.REVERT or any(d in reverted for d in deps):
                reverted.add(rid)
        eff: dict[RegionId, Decision] = {}
        for rid, _deps in order:
            if rid in reverted:
                eff[rid] = Decision.REVERT
            elif explicit.get(rid) is Decision.KEEP:
                eff[rid] = Decision.KEEP
            else:
                eff[rid] = Decision.PENDING
        self._effective_cache[path] = eff
        return eff

    def hunk_order(self, path: str) -> list[tuple[RegionId, tuple[RegionId, ...]]]:
        out: list[tuple[RegionId, tuple[RegionId, ...]]] = []
        for edit in self._edits_for(path):
            for ordinal, _change in self._changes_of(edit):
                out.append((RegionId(edit.seq, ordinal), edit.deps.get(ordinal, ())))
        return out

    def owned_hunks(self, path: str) -> list[tuple[RegionId, Owner]]:
        return [
            (RegionId(edit.seq, ordinal), edit.owner)
            for edit in self._edits_for(path)
            for ordinal, _change in self._changes_of(edit)
        ]

    def original(self, path: str) -> FileState:
        for e in self._events:
            if isinstance(e, _Edit) and e.path == path:
                return e.before
            if isinstance(e, _TurnMark) and path in e.pre:
                return e.pre[path]
        return FileState.absent()

    def compute_deps(
        self, path: str, before: FileState, after: FileState
    ) -> dict[int, tuple[RegionId, ...]]:
        """The dependencies of a new edit ``before -> after``, where ``before`` is
        the current projection. A text hunk depends on the producers of the lines
        it replaces; an insertion depends on the producer of its anchor line (the
        line above, or the line below at the top). An opaque edit is a whole-file
        barrier: it depends on everything currently applied.
        """
        lb = _decode_lines(before)
        la = _decode_lines(after)
        applied = self._applied(path, only_kept=False)
        if lb is None or la is None or after.data is None:
            return {0: tuple(sorted(applied, key=_rid_key))}
        # A whole-file existence toggle (e.g. creating an empty file) is a
        # barrier too: it sits on top of everything currently applied.
        if before.exists != after.exists and lb == la:
            return {0: tuple(sorted(applied, key=_rid_key))}

        if self._edits_for(path):
            _proj, prov = self._reconstruct_with_prov(path, applied)
        else:
            prov = [None] * len(lb)

        # A text edit built on top of an applied whole-file barrier (a deletion,
        # binary rewrite or empty-file toggle) depends on it, so reverting the
        # barrier drags the edit rather than resurrecting a state that never was.
        barriers = self._applied_barriers(path, applied)

        deps: dict[int, tuple[RegionId, ...]] = {}
        for ordinal, region in enumerate(_regions(lb, la)):
            i = region.baseline_start
            j = i + len(region.baseline_lines)
            if j > i:
                ids = {
                    prov[k]
                    for k in range(i, j)
                    if k < len(prov) and prov[k] is not None
                }
            else:
                anchor = i - 1 if i > 0 else 0
                ids = (
                    {prov[anchor]}
                    if 0 <= anchor < len(prov) and prov[anchor] is not None
                    else set()
                )
            ids.update(barriers)
            deps[ordinal] = tuple(
                sorted((r for r in ids if r is not None), key=_rid_key)
            )
        return deps

    def _applied_barriers(self, path: str, applied: set[RegionId]) -> set[RegionId]:
        """The applied whole-file (opaque) hunks for ``path`` — deletions, binary
        rewrites or empty-file toggles a later text edit must depend on.
        """
        return {
            RegionId(edit.seq, 0)
            for edit in self._edits_for(path)
            if _is_opaque(edit) and RegionId(edit.seq, 0) in applied
        }

    def scope_pending_diff(
        self, path: str, owner: Owner
    ) -> tuple[FileState, FileState] | None:
        """The scope's still-pending change: its kept hunks vs kept plus pending."""
        edit = next((e for e in self._edits_for(path) if e.owner == owner), None)
        if edit is None:
            return None
        eff = self.effective(path)
        this = [RegionId(edit.seq, ordinal) for ordinal, _c in self._changes_of(edit)]
        kept = {rid for rid in this if eff.get(rid) is Decision.KEEP}
        pending = {rid for rid in this if eff.get(rid) is Decision.PENDING}
        if not pending:
            return None
        prefix = self._applied_before(path, edit)
        baseline = self._reconstruct(path, prefix | kept)
        after = self._reconstruct(path, prefix | kept | pending)
        return baseline, after

    def pending_hunks(
        self, path: str, owner: Owner | None = None
    ) -> tuple[HunkAnchor, ...]:
        """Pending hunks in diff coordinates; ``owner`` selects the scope diff."""
        view = self._all_view(path) if owner is None else self._scope_view(path, owner)
        if view is None:
            return ()
        return tuple(self._pending_hunks(path, *view))

    def restore_plan(self, index: int) -> dict[str, FileState]:
        """Restore plan for a truncation at ``index``: each affected file's state
        as of the cut. A turn-touched file restores to the earliest dropped turn's
        recorded pre (its state when that turn began, i.e. the cut); a file only
        touched by a dropped manual edit restores to its projection of the kept
        log.
        """
        kept = History(self._events[:index])
        plan: dict[str, FileState] = {}
        for e in self._events[index:]:
            if isinstance(e, _TurnMark):
                for path, pre in e.pre.items():
                    plan.setdefault(path, pre)
        for e in self._events[index:]:
            if isinstance(e, _Edit) and e.path not in plan:
                plan[e.path] = kept.project(e.path, only_kept=False)
        return plan

    def restore_plan_to_turn(self, turn_id: int) -> dict[str, FileState]:
        if not self.has_turn(turn_id):
            return {}
        index = self.event_index_of_turn(turn_id)
        return {} if index is None else self.restore_plan(index)

    def event_index_of_turn(self, turn_id: int) -> int | None:
        exact: int | None = None
        later: int | None = None
        for i, e in enumerate(self._events):
            if not isinstance(e, _TurnMark):
                continue
            if e.turn_id == turn_id:
                exact = i
            elif e.turn_id > turn_id and later is None:
                later = i
        # Transcript resets can reuse turn ids while preserving an open turn, so
        # a stale mark may share this id — prefer the newest exact match.
        return exact if exact is not None else later

    @property
    def tracked_paths(self) -> list[str]:
        seen: dict[str, None] = {}
        for e in self._events:
            if isinstance(e, _Edit):
                seen[e.path] = None
            elif isinstance(e, _TurnMark):
                for path in e.pre:
                    seen[path] = None
        return list(seen)

    def last_turn_paths(self) -> list[str]:
        marks = self._marks
        return list(marks[-1].pre) if marks else []

    @property
    def turns(self) -> list[tuple[int, dict[str, FileState]]]:
        return [(mark.turn_id, dict(mark.pre)) for mark in self._marks]

    @property
    def scopes(self) -> list[Owner]:
        """Distinct change owners in log order; each keeps a permanent slot."""
        seen: dict[Owner, None] = {}
        for e in self._events:
            if isinstance(e, _Edit):
                seen[e.owner] = None
        return list(seen)

    def has_turn(self, turn_id: int) -> bool:
        return any(mark.turn_id == turn_id for mark in self._marks)

    def accepted_turn_frontier(self) -> int:
        accepted = 0
        for mark in self._marks:
            if not self._turn_fully_kept(mark.turn_id):
                break
            accepted += 1
        return accepted

    @property
    def _marks(self) -> list[_TurnMark]:
        return [e for e in self._events if isinstance(e, _TurnMark)]

    def _turn_fully_kept(self, turn_id: int) -> bool:
        target = AgentTurn(turn_id)
        for path in self.tracked_paths:
            eff = self.effective(path)
            for rid, owner in self.owned_hunks(path):
                if owner == target and eff.get(rid) is not Decision.KEEP:
                    return False
        return True

    def _edits_for(self, path: str) -> list[_Edit]:
        cached = self._edits_cache.get(path)
        if cached is None:
            cached = [
                e for e in self._events if isinstance(e, _Edit) and e.path == path
            ]
            self._edits_cache[path] = cached
        return cached

    def _explicit_decisions(self, path: str) -> dict[RegionId, Decision]:
        """The keep/revert recorded per hunk (ratchet: once REVERT, KEEPs ignored)."""
        decisions: dict[RegionId, Decision] = {}
        for ev in self._events:
            if isinstance(ev, _Decide) and ev.path == path:
                if decisions.get(ev.hunk) is Decision.REVERT:
                    continue
                decisions[ev.hunk] = Decision.KEEP if ev.keep else Decision.REVERT
        return decisions

    def _applied(self, path: str, *, only_kept: bool) -> set[RegionId]:
        """Hunks to apply for a projection, dependency-closed. ``only_kept``
        selects the accepted baseline (KEEP only); otherwise the on-disk state
        (everything not reverted). Hunks are ordered so a hunk's dependencies
        precede it.
        """
        eff = self.effective(path)
        applied: set[RegionId] = set()
        for rid, deps in self.hunk_order(path):
            decision = eff[rid]
            if decision is Decision.REVERT:
                continue
            if only_kept and decision is not Decision.KEEP:
                continue
            if all(dep in applied for dep in deps):
                applied.add(rid)
        return applied

    def _applied_before(self, path: str, target: _Edit) -> set[RegionId]:
        """Hunks applied (non-reverted, dependency-closed) from the edits
        preceding ``target`` in log order — the baseline a turn's own change sits
        on. Uses list order, since a captured drift edit can carry a higher seq
        than the turn marker it was inserted before.
        """
        eff = self.effective(path)
        applied: set[RegionId] = set()
        for e in self._edits_for(path):
            if e is target:
                break
            for ordinal, _change in self._changes_of(e):
                rid = RegionId(e.seq, ordinal)
                if eff[rid] is Decision.REVERT:
                    continue
                if all(dep in applied for dep in e.deps.get(ordinal, ())):
                    applied.add(rid)
        return applied

    def _reconstruct(self, path: str, applied: set[RegionId]) -> FileState:
        """Rebuild the file by replaying the applied hunks of each edit in order.
        Each text edit splices against its own ``before`` rebased onto the running
        result; an opaque edit replaces the whole file. Closure guarantees every
        splice lands on its exact base.
        """
        edits = self._edits_for(path)
        if not edits:
            return self.original(path)
        ref = _reference_state(edits)
        result = edits[0].before
        for edit in edits:
            here = [
                change
                for ordinal, change in self._changes_of(edit)
                if RegionId(edit.seq, ordinal) in applied
            ]
            if not here:
                continue
            if _is_opaque(edit):
                result = edit.after
            else:
                lines = _lines(result)
                _splice(
                    lines,
                    _lines(edit.before),
                    [c for c in here if isinstance(c, Region)],
                )
                result = _reencode("".join(lines), ref)
        return result

    def _reconstruct_with_prov(
        self, path: str, applied: set[RegionId]
    ) -> tuple[list[str], list[RegionId | None]]:
        """Rebuild the text projection and, in parallel, the provenance of every
        line: the hunk that last wrote it (None for original lines). Used at
        append time to attribute a new edit's dependencies.
        """
        edits = self._edits_for(path)
        result = _lines(edits[0].before) if edits else []
        prov: list[RegionId | None] = [None] * len(result)
        for edit in edits:
            here = [
                (RegionId(edit.seq, ordinal), change)
                for ordinal, change in self._changes_of(edit)
                if RegionId(edit.seq, ordinal) in applied
            ]
            if not here:
                continue
            if _is_opaque(edit):
                result = _lines(edit.after)
                prov = [RegionId(edit.seq, 0)] * len(result)
            else:
                _splice_with_prov(
                    result,
                    prov,
                    _lines(edit.before),
                    [(rid, c) for rid, c in here if isinstance(c, Region)],
                )
        return result, prov

    def _all_view(
        self, path: str
    ) -> tuple[set[RegionId], set[RegionId], set[RegionId]] | None:
        """(baseline, current, pending) for the whole-file diff: KEEP-only vs disk."""
        if not self._edits_for(path):
            return None
        eff = self.effective(path)
        applied_current = self._applied(path, only_kept=False)
        applied_baseline = self._applied(path, only_kept=True)
        pending = {rid for rid in applied_current if eff.get(rid) is Decision.PENDING}
        return applied_baseline, applied_current, pending

    def _scope_view(
        self, path: str, owner: Owner
    ) -> tuple[set[RegionId], set[RegionId], set[RegionId]] | None:
        """(baseline, current, pending) for a scope diff, mirroring
        ``scope_pending_diff``: the scope's kept hunks against those plus pending.
        """
        edit = next((e for e in self._edits_for(path) if e.owner == owner), None)
        if edit is None:
            return None
        eff = self.effective(path)
        this = [RegionId(edit.seq, ordinal) for ordinal, _c in self._changes_of(edit)]
        pending = {rid for rid in this if eff.get(rid) is Decision.PENDING}
        if not pending:
            return None
        kept = {rid for rid in this if eff.get(rid) is Decision.KEEP}
        prefix = self._applied_before(path, edit)
        return prefix | kept, prefix | kept | pending, pending

    def _pending_components(
        self, path: str, pending: set[RegionId]
    ) -> dict[RegionId, frozenset[RegionId]]:
        """Group the pending hunks into dependency-connected components (via
        ``dependsOn`` edges), mapping each hunk to its whole component. A rendered
        hunk decides its full component together, so reverting it drags every hunk
        built on the same lines and keeping it pulls in the ones it is built on.
        """
        order = [(rid, deps) for rid, deps in self.hunk_order(path) if rid in pending]
        parent = {rid: rid for rid, _ in order}

        def find(x: RegionId) -> RegionId:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for rid, deps in order:
            for dep in deps:
                if dep in parent:
                    parent[find(rid)] = find(dep)
        components: dict[RegionId, set[RegionId]] = {}
        for rid in parent:
            components.setdefault(find(rid), set()).add(rid)
        return {rid: frozenset(components[find(rid)]) for rid in parent}

    def _pending_hunks(
        self,
        path: str,
        applied_baseline: set[RegionId],
        applied_current: set[RegionId],
        pending: set[RegionId],
    ) -> list[HunkAnchor]:
        """Locate every pending change in the rendered ``baseline -> current``
        diff.

        ``current`` carries per-line provenance, so an addition or edit attributes
        to the pending hunk that wrote its lines. A pure deletion leaves no
        current line, so it is matched by its removed content against the pending
        deletions. Each hunk's target is expanded to the full dependency
        component, so a decision on it never leaves a superseded hunk from an
        earlier turn stranded.
        """
        base_lines = _lines(self._reconstruct(path, applied_baseline))
        cur_lines, cur_prov = self._reconstruct_with_prov(path, applied_current)
        components = self._pending_components(path, pending)
        deletions = [
            (RegionId(edit.seq, ordinal), change.baseline_lines)
            for edit in self._edits_for(path)
            for ordinal, change in self._changes_of(edit)
            if RegionId(edit.seq, ordinal) in pending
            and isinstance(change, Region)
            and not change.current_lines
        ]
        anchors: list[HunkAnchor] = []
        consumed: set[RegionId] = set()
        for tag, i1, i2, j1, j2 in SequenceMatcher(
            a=base_lines, b=cur_lines, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            seeds = {
                rid
                for k in range(j1, j2)
                if (rid := cur_prov[k]) is not None and rid in pending
            }
            if tag == "delete":
                removed = base_lines[i1:i2]
                seeds.update(_match_deletions(removed, deletions, consumed))
                side: HunkSide = "deletions"
                # Anchor on the last line of the block so the inline control
                # renders right after the whole change, not inside a multi-line
                # hunk.
                line = i2 - 1
            else:
                side = "additions"
                line = j2 - 1
            regions: set[RegionId] = set()
            for rid in seeds:
                regions |= components.get(rid, {rid})
            if regions:
                anchors.append(
                    HunkAnchor(side, line, tuple(sorted(regions, key=_rid_key)))
                )
        return anchors
