from __future__ import annotations

from vibe.core.checkpoints import AgentTurn, Checkpointer, Decision, FileState


def _txt(text: str) -> FileState:
    return FileState.from_text(text)


def _turn(cp: Checkpointer, turn_id: int, path: str, pre: str, post: str) -> None:
    cp.begin_turn(turn_id)
    cp.record_pre_edit(path, _txt(pre))
    cp.record_post_edit(path, _txt(post))
    cp.seal_turn()


def _pending_rids(cp: Checkpointer, current: FileState) -> set[tuple[int, int]]:
    return {
        (tr.region_id.version_index, tr.region_id.ordinal)
        for tr in cp.view({"f": current}).regions("f")
        if tr.decision is Decision.PENDING
    }


def _rids(anchor) -> set[tuple[int, int]]:  # type: ignore[no-untyped-def]
    return {(r.version_index, r.ordinal) for r in anchor.regions}


class TestAllView:
    def test_single_edit_anchors_additions_side(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\nd\ne\n", "a\nb\nC\nd\ne\n")
        current = _txt("a\nb\nC\nd\ne\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        assert [(h.side, h.line) for h in hunks] == [("additions", 2)]
        # Every pending region is covered exactly once.
        assert set().union(*(_rids(h) for h in hunks)) == _pending_rids(cp, current)

    def test_two_disjoint_hunks_get_one_anchor_each(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\nd\ne\nf\n", "a\nB\nc\nd\nE\nf\n")
        current = _txt("a\nB\nc\nd\nE\nf\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        assert [(h.side, h.line) for h in hunks] == [("additions", 1), ("additions", 4)]
        # Distinct regions, all pending covered.
        assert _rids(hunks[0]).isdisjoint(_rids(hunks[1]))
        assert set().union(*(_rids(h) for h in hunks)) == _pending_rids(cp, current)

    def test_insertion_anchors_at_inserted_line(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "a\nX\nb\n")
        current = _txt("a\nX\nb\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        assert [(h.side, h.line) for h in hunks] == [("additions", 1)]

    def test_multiline_edit_anchors_last_current_line(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\nd\n", "a\nB\nC\nd\n")
        current = _txt("a\nB\nC\nd\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        # The control renders after the whole block, so it anchors on the last
        # changed line (C), not the first (B).
        assert [(h.side, h.line) for h in hunks] == [("additions", 2)]
        assert set().union(*(_rids(h) for h in hunks)) == _pending_rids(cp, current)

    def test_multiline_insertion_anchors_last_inserted_line(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "a\nX\nY\nb\n")
        current = _txt("a\nX\nY\nb\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        assert [(h.side, h.line) for h in hunks] == [("additions", 2)]

    def test_pure_deletion_anchors_last_removed_line(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\nd\n", "a\nd\n")
        current = _txt("a\nd\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        # Multi-line deletion: anchor on the last removed line (c), so the control
        # renders right after the removed block.
        assert [(h.side, h.line) for h in hunks] == [("deletions", 2)]
        assert set().union(*(_rids(h) for h in hunks)) == _pending_rids(cp, current)

    def test_identical_independent_deletions_get_separate_anchors(self) -> None:
        cp = Checkpointer()
        # Two independent deletions of the same line "x": each inline control must
        # decide only its own region, not both.
        _turn(cp, 1, "f", "x\na\nx\nb\n", "a\nb\n")
        current = _txt("a\nb\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        dels = [h for h in hunks if h.side == "deletions"]
        assert len(dels) == 2
        assert _rids(dels[0]).isdisjoint(_rids(dels[1]))
        assert set().union(*(_rids(h) for h in dels)) == _pending_rids(cp, current)

    def test_cross_turn_edit_at_same_line_is_one_anchor(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "a\nB1\nc\n")
        _turn(cp, 2, "f", "a\nB1\nc\n", "a\nB2\nc\n")
        current = _txt("a\nB2\nc\n")

        hunks = cp.view({"f": current}).pending_hunks("f")

        # v0 -> disk shows one changed line; both turns' hunks decide together.
        assert [(h.side, h.line) for h in hunks] == [("additions", 1)]
        assert _rids(hunks[0]) == _pending_rids(cp, current)
        assert len(hunks[0].regions) == 2

    def test_reverted_hunk_is_excluded(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\nd\ne\nf\n", "a\nB\nc\nd\nE\nf\n")
        current = _txt("a\nB\nc\nd\nE\nf\n")
        first = cp.view({"f": current}).regions("f")[0]
        cp.decide_region("f", first.region_id, Decision.REVERT)

        hunks = cp.view({"f": current}).pending_hunks("f")

        # Only the surviving pending hunk keeps an anchor.
        assert len(hunks) == 1
        assert first.region_id not in {r for h in hunks for r in h.regions}

    def test_nothing_pending_yields_no_hunks(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "A\nb\n")
        current = _txt("A\nb\n")
        cp.decide_file("f", Decision.KEEP)

        assert cp.view({"f": current}).pending_hunks("f") == ()

    def test_untracked_file_yields_no_hunks(self) -> None:
        cp = Checkpointer()
        assert cp.view({"f": _txt("a\n")}).pending_hunks("f") == ()


class TestScopeView:
    def test_scope_view_anchors_only_that_scope(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\nd\ne\n", "a\nB\nc\nd\ne\n")
        _turn(cp, 2, "f", "a\nB\nc\nd\ne\n", "a\nB\nc\nd\nE\n")
        current = _txt("a\nB\nc\nd\nE\n")

        hunks = cp.view({"f": current}).pending_hunks("f", AgentTurn(2))

        # Only turn 2's edit (line 4) is in scope; turn 1's kept out of the diff.
        assert [(h.side, h.line) for h in hunks] == [("additions", 4)]
        turn2_rids = {
            (tr.region_id.version_index, tr.region_id.ordinal)
            for tr in cp.view({"f": current}).regions("f")
            if tr.owner == AgentTurn(2)
        }
        assert set().union(*(_rids(h) for h in hunks)) == turn2_rids

    def test_scope_view_none_when_scope_absent(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", "A\n")
        current = _txt("A\n")

        assert cp.view({"f": current}).pending_hunks("f", AgentTurn(99)) == ()
