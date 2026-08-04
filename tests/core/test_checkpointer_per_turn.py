from __future__ import annotations

import pytest

from vibe.core.checkpoints import (
    AgentTurn,
    Checkpointer,
    Decision,
    FileState,
    FileStateError,
    ManualEdit,
    OpaqueChange,
    OpaqueReason,
    TurnRegion,
    TurnStateError,
)


def _txt(text: str) -> FileState:
    return FileState.from_text(text)


def _turn(
    cp: Checkpointer, turn_id: int, path: str, pre: str, post: str | None
) -> None:
    cp.begin_turn(turn_id)
    cp.record_pre_edit(path, _txt(pre))
    # A post seals the turn; leaving post None keeps it open (live-tail folding).
    if post is not None:
        cp.record_post_edit(path, _txt(post))
        cp.seal_turn()


def _view(cp: Checkpointer, current: FileState) -> tuple[TurnRegion, ...]:
    view = cp.view({"f": current}).regions("f")
    assert isinstance(view, tuple)
    return view


class TestAttribution:
    def test_disjoint_turns_attributed_to_their_turn(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nc\n")
        _turn(cp, 2, "f", "A\nb\nc\n", "A\nb\nC\n")
        current = _txt("A\nb\nC\n")

        assert {tr.owner for tr in _view(cp, current)} == {AgentTurn(1), AgentTurn(2)}

    def test_user_edit_after_sealed_turn_is_a_local_layer(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nc\n")
        current = _txt("A\nb\nC\n")  # user edited line 3 after the turn sealed
        cp.reconcile("f", current)  # render boundary captures the manual edit

        assert {tr.owner for tr in _view(cp, current)} == {ManualEdit(1), AgentTurn(1)}

    def test_open_last_turn_folds_its_live_tail(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", None)  # not sealed
        current = _txt("b\n")

        view = _view(cp, current)
        assert [tr.owner for tr in view] == [AgentTurn(1)]


class TestPerTurnRevert:
    @staticmethod
    def _two_turns() -> tuple[Checkpointer, FileState]:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nc\n")
        _turn(cp, 2, "f", "A\nb\nc\n", "A\nb\nC\n")
        return cp, _txt("A\nb\nC\n")

    def test_revert_one_turn_keeps_the_other(self) -> None:
        cp, current = self._two_turns()

        cp.decide_scope("f", AgentTurn(1), Decision.REVERT)

        # Turn 1's line-1 edit undone; turn 2's line-3 edit survives on the
        # same file — the headline P2 behavior.
        assert cp.view({"f": current}).content("f") == _txt("a\nb\nC\n")

    def test_revert_later_turn_keeps_earlier(self) -> None:
        cp, current = self._two_turns()

        cp.decide_scope("f", AgentTurn(2), Decision.REVERT)

        assert cp.view({"f": current}).content("f") == _txt("A\nb\nc\n")

    def test_revert_local_layer_keeps_model_turn(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nc\n")
        current = _txt("A\nb\nC\n")

        cp.decide_scope("f", ManualEdit(1), Decision.REVERT)

        assert cp.view({"f": current}).content("f") == _txt("A\nb\nc\n")


class TestIncrementalPersist:
    """Reverting turns one at a time and persisting between each — the manager's
    decide-then-write flow — must round-trip to the original. The persisted
    intermediate disk trails the newest decision, so it must not be mistaken for
    a user edit and re-folded as a phantom layer.
    """

    def test_revert_deletion_turn_then_edit_turn_restores_original(self) -> None:
        # Regression: reverting the deletion turn first restores lines 3,4 and
        # persists; reverting the later edit then previously duplicated 3,4.
        cp = Checkpointer()
        _turn(cp, 1, "f", "1\n2\n3\n4\n5\n6\n", "1\n2\n5\n6\n")  # delete lines 3,4
        _turn(cp, 2, "f", "1\n2\n5\n6\n", "1\n2\n5\n6x\n")  # edit line 6

        disk = _txt("1\n2\n5\n6x\n")
        cp.decide_scope("f", AgentTurn(1), Decision.REVERT)
        disk = cp.view({"f": disk}).content("f")  # manager persists this
        assert disk == _txt("1\n2\n3\n4\n5\n6x\n")

        cp.decide_scope("f", AgentTurn(2), Decision.REVERT)
        disk = cp.view({"f": disk}).content("f")
        assert disk == _txt("1\n2\n3\n4\n5\n6\n")  # original, not duplicated
        assert cp.view({"f": disk}).is_fully_reviewed("f")

    def test_incremental_revert_of_three_turns_restores_original(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "1\n2\n3\n4\n5\n", "1\n2\n5\n")  # delete lines 3,4
        _turn(cp, 2, "f", "1\n2\n5\n", "0\n1\n2\n5\n")  # insert at top
        _turn(cp, 3, "f", "0\n1\n2\n5\n", "0\n1\n2\n5z\n")  # edit last line

        disk = _txt("0\n1\n2\n5z\n")
        for turn_id in (1, 2, 3):
            cp.decide_scope("f", AgentTurn(turn_id), Decision.REVERT)
            disk = cp.view({"f": disk}).content("f")

        assert disk == _txt("1\n2\n3\n4\n5\n")
        assert cp.view({"f": disk}).is_fully_reviewed("f")


class TestCascade:
    @staticmethod
    def _stacked() -> tuple[Checkpointer, FileState]:
        cp = Checkpointer()
        _turn(cp, 1, "f", "x\n", "y\n")
        _turn(cp, 2, "f", "y\n", "z\n")
        return cp, _txt("z\n")

    def test_revert_earlier_cascades_to_dependent_later(self) -> None:
        cp, current = self._stacked()

        cp.decide_scope("f", AgentTurn(1), Decision.REVERT)

        assert cp.view({"f": current}).content("f") == _txt("x\n")
        assert all(tr.decision is Decision.REVERT for tr in _view(cp, current))

    def test_keep_later_cascades_to_earlier(self) -> None:
        cp, current = self._stacked()

        cp.decide_scope("f", AgentTurn(2), Decision.KEEP)

        assert all(tr.decision is Decision.KEEP for tr in _view(cp, current))
        assert cp.view({"f": current}).is_fully_reviewed("f")
        assert cp.view({"f": current}).content("f") == current

    def test_dependent_later_hunk_declares_dependency(self) -> None:
        cp, current = self._stacked()

        by_turn = {tr.owner: tr for tr in _view(cp, current)}
        assert by_turn[AgentTurn(1)].depends_on == ()
        assert by_turn[AgentTurn(2)].depends_on == (by_turn[AgentTurn(1)].region_id,)


class TestPerRegion:
    def test_revert_single_region_within_a_turn(self) -> None:
        cp = Checkpointer()
        # one turn changes two disjoint lines
        _turn(cp, 1, "f", "1\nx\n2\n", "A\nx\nB\n")
        current = _txt("A\nx\nB\n")

        view = _view(cp, current)
        first = min(view, key=lambda tr: tr.region_id.ordinal)
        cp.decide_region("f", first.region_id, Decision.REVERT)

        assert cp.view({"f": current}).content("f") == _txt("1\nx\nB\n")


class TestReconstruction:
    def test_all_pending_reproduces_current(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "A\nb\n")
        _turn(cp, 2, "f", "A\nb\n", "A\nB\n")
        current = _txt("A\nB\n")

        assert cp.view({"f": current}).content("f") == current

    def test_interleaved_edits_and_decisions_are_consistent(self) -> None:
        # turn 1 edits line 1; user edits line 3 (local); turn 2 edits line 5.
        cp = Checkpointer()
        _turn(cp, 1, "f", "1\n2\n3\n4\n5\n", "A\n2\n3\n4\n5\n")
        # user edit after turn 1 seal, before turn 2 pre (captured as turn 2's pre)
        _turn(cp, 2, "f", "A\n2\nC\n4\n5\n", "A\n2\nC\n4\nE\n")
        current = _txt("A\n2\nC\n4\nE\n")

        owners = {tr.owner for tr in _view(cp, current)}
        assert owners == {ManualEdit(1), AgentTurn(1), AgentTurn(2)}

        # revert the model turns, keep the user's own edit
        cp.decide_scope("f", AgentTurn(1), Decision.REVERT)
        cp.decide_scope("f", AgentTurn(2), Decision.REVERT)
        assert cp.view({"f": current}).content("f") == _txt("1\n2\nC\n4\n5\n")


class TestDerivedBaseline:
    def test_accepted_baseline_holds_only_approved_hunks(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nc\n")
        _turn(cp, 2, "f", "A\nb\nc\n", "A\nb\nC\n")
        current = _txt("A\nb\nC\n")

        # nothing approved yet: accepted floor is the original
        assert cp.view({"f": current}).accepted_baseline("f") == _txt("a\nb\nc\n")

        cp.decide_scope("f", AgentTurn(1), Decision.KEEP)

        # turn 1 approved: floor advances to include only its hunk; disk unchanged
        assert cp.view({"f": current}).accepted_baseline("f") == _txt("A\nb\nc\n")
        assert cp.view({"f": current}).content("f") == current

    def test_approve_leaves_disk_untouched_revert_changes_it(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", "A\n")
        current = _txt("A\n")

        cp.decide_scope("f", AgentTurn(1), Decision.KEEP)
        assert cp.view({"f": current}).content("f") == _txt("A\n")

        cp2 = Checkpointer()
        _turn(cp2, 1, "f", "a\n", "A\n")
        cp2.decide_scope("f", AgentTurn(1), Decision.REVERT)
        assert cp2.view({"f": current}).content("f") == _txt("a\n")

    def test_frontier_counts_leading_fully_kept_turns(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "A\nb\n")
        _turn(cp, 2, "f", "A\nb\n", "A\nB\n")

        assert cp.view().accepted_turn_frontier() == 0

        cp.decide_scope("f", AgentTurn(1), Decision.KEEP)
        assert cp.view().accepted_turn_frontier() == 1

        cp.decide_scope("f", AgentTurn(2), Decision.KEEP)
        assert cp.view().accepted_turn_frontier() == 2

    def test_is_agent_owned_tracks_pending_changes(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", "A\n")
        current = _txt("A\n")

        assert cp.view().original("f") != current
        assert not (cp.view().original("f") != _txt("a\n"))


class TestSnapshotLogAndRewind:
    def test_record_before_begin_turn_raises(self) -> None:
        cp = Checkpointer()
        with pytest.raises(FileStateError):
            cp.record_pre_edit("f", _txt("x"))

    def test_restore_plan_holds_pre_range_state(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "v0\n", "v1\n")
        _turn(cp, 2, "f", "v1\n", "v2\n")

        assert cp.view().restore_plan_to_turn(2) == {"f": _txt("v1\n")}
        assert cp.view().restore_plan_to_turn(1) == {"f": _txt("v0\n")}

    def test_rewind_drops_decisions_made_after_the_cut(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", "A\n")
        _turn(cp, 2, "f", "A\n", "B\n")
        cp.decide_scope("f", AgentTurn(1), Decision.KEEP)  # recorded after turn 2 began

        cp.drop_turns_from(2)

        # Rewind truncates the log tail, so a decision made after the cut is
        # dropped with it: turn 1's hunk is pending again.
        remaining = _txt("A\n")
        assert all(tr.decision is Decision.PENDING for tr in _view(cp, remaining))


class TestOpaquePerTurn:
    @staticmethod
    def _binary_turn(
        cp: Checkpointer, turn_id: int, path: str, pre: bytes, post: bytes
    ) -> None:
        cp.begin_turn(turn_id)
        cp.record_pre_edit(path, FileState(pre))
        cp.record_post_edit(path, FileState(post))
        cp.seal_turn()

    def test_binary_change_is_one_opaque_region_per_turn(self) -> None:
        cp = Checkpointer()
        self._binary_turn(cp, 1, "img", b"\x00old", b"\x00new")
        current = FileState(b"\x00new")

        view = cp.view({"img": current}).regions("img")
        assert len(view) == 1
        assert view[0].owner == AgentTurn(1)
        assert isinstance(view[0].change, OpaqueChange)
        assert view[0].change.reason is OpaqueReason.BINARY_OR_UNDECODABLE

    def test_binary_revert_restores_original_approve_keeps(self) -> None:
        cp = Checkpointer()
        self._binary_turn(cp, 1, "img", b"\x00old", b"\x00new")
        current = FileState(b"\x00new")

        cp.decide_file("img", Decision.REVERT)
        assert cp.view({"img": current}).content("img") == FileState(b"\x00old")

        cp2 = Checkpointer()
        self._binary_turn(cp2, 1, "img", b"\x00old", b"\x00new")
        cp2.decide_file("img", Decision.KEEP)
        assert cp2.view({"img": current}).content("img") == current
        assert cp2.view({"img": current}).accepted_baseline("img") == current

    def test_binary_per_turn_revert_keeps_earlier(self) -> None:
        cp = Checkpointer()
        self._binary_turn(cp, 1, "img", b"\x00v0", b"\x00v1")
        self._binary_turn(cp, 2, "img", b"\x00v1", b"\x00v2")
        current = FileState(b"\x00v2")

        cp.decide_scope("img", AgentTurn(2), Decision.REVERT)
        assert cp.view({"img": current}).content("img") == FileState(b"\x00v1")

    def test_binary_revert_earlier_cascades_to_later(self) -> None:
        cp = Checkpointer()
        self._binary_turn(cp, 1, "img", b"\x00v0", b"\x00v1")
        self._binary_turn(cp, 2, "img", b"\x00v1", b"\x00v2")
        current = FileState(b"\x00v2")

        cp.decide_scope("img", AgentTurn(1), Decision.REVERT)
        assert cp.view({"img": current}).content("img") == FileState(b"\x00v0")

    def test_turn_deletion_is_opaque_and_revertible(self) -> None:
        cp = Checkpointer()
        cp.begin_turn(1)
        cp.record_pre_edit("f", _txt("content\n"))
        cp.record_post_edit("f", FileState.absent())
        cp.seal_turn()
        current = FileState.absent()

        view = cp.view({"f": current}).regions("f")
        assert len(view) == 1
        assert view[0].owner == AgentTurn(1)
        assert isinstance(view[0].change, OpaqueChange)
        assert view[0].change.reason is OpaqueReason.MISSING

        cp.decide_file("f", Decision.REVERT)
        assert cp.view({"f": current}).content("f") == _txt("content\n")

    def test_user_deletion_after_seal_is_a_local_opaque_layer(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "content\n", "content\n")  # sealed, no net change

        cp.reconcile("f", FileState.absent())  # user deletes the file after the turn
        view = cp.view({"f": FileState.absent()}).regions("f")
        assert [tr.owner for tr in view] == [ManualEdit(1)]
        assert isinstance(view[0].change, OpaqueChange)
        assert view[0].change.reason is OpaqueReason.MISSING

    def test_manual_drift_after_noop_turn_is_captured(self) -> None:
        cp = Checkpointer()
        # Turn 1 touches f but leaves it unchanged: the path is tracked, yet no
        # edit is recorded for it.
        cp.begin_turn(1)
        cp.record_pre_edit("f", _txt("a\n"))
        cp.record_post_edit("f", _txt("a\n"))
        cp.seal_turn()
        # The user edits f between turns; the next turn's first read sees the drift.
        cp.begin_turn(2)
        cp.record_pre_edit("f", _txt("A\n"))
        cp.record_post_edit("f", _txt("A\n"))
        cp.seal_turn()

        view = cp.view({"f": _txt("A\n")}).regions("f")
        assert [tr.owner for tr in view] == [ManualEdit(1)]


class TestTurnGate:
    @staticmethod
    def _open_turn() -> tuple[Checkpointer, FileState]:
        cp = Checkpointer()
        cp.begin_turn(1)
        cp.record_pre_edit("f", _txt("a\n"))
        cp.record_post_edit("f", _txt("A\n"))  # captured but not yet sealed
        return cp, _txt("A\n")

    def test_decisions_refused_while_turn_open(self) -> None:
        cp, current = self._open_turn()
        view = cp.view({"f": current}).regions("f")
        assert isinstance(view, tuple)

        with pytest.raises(TurnStateError):
            cp.decide_file("f", Decision.REVERT)
        with pytest.raises(TurnStateError):
            cp.decide_scope("f", AgentTurn(1), Decision.REVERT)
        with pytest.raises(TurnStateError):
            cp.decide_region("f", view[0].region_id, Decision.KEEP)

    def test_reads_allowed_while_turn_open(self) -> None:
        cp, current = self._open_turn()

        assert isinstance(cp.view({"f": current}).regions("f"), tuple)
        assert cp.view({"f": current}).content("f") == current
        assert cp.view({"f": current}).accepted_baseline("f") == _txt("a\n")

    def test_decisions_allowed_after_seal(self) -> None:
        cp, current = self._open_turn()
        cp.seal_turn()

        cp.decide_file("f", Decision.REVERT)
        assert cp.view({"f": current}).content("f") == _txt("a\n")

    def test_begin_turn_while_open_raises(self) -> None:
        cp = Checkpointer()
        cp.begin_turn(1)
        with pytest.raises(TurnStateError):
            cp.begin_turn(2)

    def test_begin_turn_allowed_after_seal(self) -> None:
        cp = Checkpointer()
        cp.begin_turn(1)
        cp.seal_turn()
        cp.begin_turn(2)

    def test_record_requires_open_turn(self) -> None:
        cp = Checkpointer()
        cp.begin_turn(1)
        cp.seal_turn()
        with pytest.raises(TurnStateError):
            cp.record_pre_edit("f", _txt("a\n"))

    def test_seal_turn_is_idempotent(self) -> None:
        cp = Checkpointer()
        cp.begin_turn(1)
        cp.seal_turn()
        cp.seal_turn()


class TestRevertPersistNoPhantom:
    def test_full_revert_disk_has_no_phantom_local_layer(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", "A\n")
        cp.decide_file("f", Decision.REVERT)

        # The shell persists review_content to disk: it is now "a\n".
        reverted = _txt("a\n")
        view = _view(cp, reverted)

        assert [tr.owner for tr in view] == [AgentTurn(1)]  # only the agent hunk
        assert view[0].decision is Decision.REVERT
        assert cp.view({"f": reverted}).content("f") == reverted

    def test_partial_revert_disk_has_no_phantom_local_layer(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "1\nx\n2\n", "A\nx\nB\n")
        current = _txt("A\nx\nB\n")
        first = min(_view(cp, current), key=lambda tr: tr.region_id.ordinal)
        cp.decide_region("f", first.region_id, Decision.REVERT)

        reverted = _txt("1\nx\nB\n")  # persisted result of reverting the first hunk
        view = _view(cp, reverted)

        assert [tr.owner for tr in view] == [AgentTurn(1), AgentTurn(1)]  # two hunks
        assert cp.view({"f": reverted}).content("f") == reverted

    def test_genuine_user_edit_still_surfaces_after_decision(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "A\nb\n")
        cp.decide_scope("f", AgentTurn(1), Decision.KEEP)

        # User then edits line 2 on disk, on top of the kept agent edit.
        edited = _txt("A\nB\n")
        cp.reconcile("f", edited)  # render boundary captures the manual edit
        view = _view(cp, edited)

        assert any(isinstance(tr.owner, ManualEdit) for tr in view)
        assert cp.view({"f": edited}).content("f") == edited


class TestDecisionStability:
    def test_decision_survives_chain_reshape(self) -> None:
        # Decisions are keyed by a position in the derived version chain; this
        # guards that a decision keeps pointing at the same hunk once the chain
        # grows (a between-turn user edit plus a later turn).
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "A\nb\n")  # turn 1 edits line 1
        current1 = _txt("A\nb\n")
        kept_id = _view(cp, current1)[0].region_id
        cp.decide_region("f", kept_id, Decision.KEEP)

        # Reshape: a between-turn user edit to line 2 (a LOCAL layer), then turn 2
        # edits it again — the chain grows from two versions to four.
        _turn(cp, 2, "f", "A\nB\n", "A\nB2\n")
        current2 = _txt("A\nB2\n")

        by_id = {tr.region_id: tr for tr in _view(cp, current2)}
        assert kept_id in by_id
        assert by_id[kept_id].owner == AgentTurn(1)
        assert by_id[kept_id].decision is Decision.KEEP
        # The KEEP still materializes: only turn 1's edit sits in the baseline.
        assert cp.view({"f": current2}).accepted_baseline("f") == _txt("A\nb\n")


class TestClosureOnRead:
    def test_reverting_ground_drags_the_dependent_and_reconstructs_cleanly(
        self,
    ) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "x\n", "y\n")
        _turn(cp, 2, "f", "y\n", "z\n")  # built on turn 1's ground
        current = _txt("z\n")
        by_turn = {tr.owner: tr for tr in _view(cp, current)}

        # Reverting the ground drags the dependent by closure — a projection never
        # applies a hunk whose base is gone, so reconstruction stays a clean splice
        # (bottoms out at the original), never a merge.
        cp.decide_region("f", by_turn[AgentTurn(1)].region_id, Decision.REVERT)

        assert cp.view({"f": current}).content("f") == _txt("x\n")
        assert cp.view({"f": current}).accepted_baseline("f") == _txt("x\n")
        assert cp.view({"f": current}).is_fully_reviewed("f")


class TestTurnPendingDiff:
    def test_each_turn_pending_diff_is_its_own_change(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nc\n")
        _turn(cp, 2, "f", "A\nb\nc\n", "A\nb\nC\n")
        current = _txt("A\nb\nC\n")

        assert cp.view({"f": current}).scope_pending_diff("f", AgentTurn(1)) == (
            _txt("a\nb\nc\n"),
            _txt("A\nb\nc\n"),
        )
        assert cp.view({"f": current}).scope_pending_diff("f", AgentTurn(2)) == (
            _txt("A\nb\nc\n"),
            _txt("A\nb\nC\n"),
        )

    def test_kept_hunk_drops_out_of_the_turns_pending_diff(self) -> None:
        cp = Checkpointer()
        # One turn, two disjoint hunks (line 1 and line 3).
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nC\n")
        current = _txt("A\nb\nC\n")
        by_ordinal = {tr.region_id.ordinal: tr.region_id for tr in _view(cp, current)}
        cp.decide_region("f", by_ordinal[0], Decision.KEEP)

        # The kept first-line hunk moves into the baseline; only the pending
        # third-line hunk is left to review.
        assert cp.view({"f": current}).scope_pending_diff("f", AgentTurn(1)) == (
            _txt("A\nb\nc\n"),
            _txt("A\nb\nC\n"),
        )

    def test_fully_decided_turn_has_no_pending_diff(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nC\n")
        current = _txt("A\nb\nC\n")
        cp.decide_scope("f", AgentTurn(1), Decision.KEEP)

        assert cp.view({"f": current}).scope_pending_diff("f", AgentTurn(1)) is None

    def test_turn_that_did_not_touch_file_has_no_diff(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", "A\n")

        assert cp.view({"f": _txt("A\n")}).scope_pending_diff("f", AgentTurn(2)) is None

    def test_created_file_pending_diff_starts_from_absent(self) -> None:
        cp = Checkpointer()
        cp.begin_turn(1)
        cp.record_pre_edit("f", FileState.absent())
        cp.record_post_edit("f", _txt("new\n"))
        cp.seal_turn()

        assert cp.view({"f": _txt("new\n")}).scope_pending_diff("f", AgentTurn(1)) == (
            FileState.absent(),
            _txt("new\n"),
        )


class TestBulkDecisionSkipsDecided:
    def test_decide_file_leaves_already_decided_hunks_untouched(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nC\n")
        current = _txt("A\nb\nC\n")
        by_ordinal = {tr.region_id.ordinal: tr.region_id for tr in _view(cp, current)}
        cp.decide_region("f", by_ordinal[1], Decision.REVERT)
        current = cp.view({"f": current}).content("f")  # reverting rewrote disk

        # A file-wide approve must not flip the hunk already reverted.
        cp.decide_file("f", Decision.KEEP)

        decisions = {tr.region_id: tr.decision for tr in _view(cp, current)}
        assert decisions[by_ordinal[0]] is Decision.KEEP
        assert decisions[by_ordinal[1]] is Decision.REVERT

    def test_decide_turn_leaves_already_decided_hunks_untouched(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "A\nb\nC\n")
        current = _txt("A\nb\nC\n")
        by_ordinal = {tr.region_id.ordinal: tr.region_id for tr in _view(cp, current)}
        cp.decide_region("f", by_ordinal[0], Decision.KEEP)

        cp.decide_scope("f", AgentTurn(1), Decision.REVERT)
        current = cp.view({"f": current}).content("f")  # reverting rewrote disk

        decisions = {tr.region_id: tr.decision for tr in _view(cp, current)}
        assert decisions[by_ordinal[0]] is Decision.KEEP
        assert decisions[by_ordinal[1]] is Decision.REVERT


class TestManualEditDependency:
    def test_overlapping_manual_edit_is_dragged_by_its_turns_revert(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\nc\n", "a\nB\nc\n")  # turn 1 edits line 2 -> B
        disk = _txt("a\nB2\nc\n")  # user edits line 2 further, on top of turn 1
        cp.reconcile("f", disk)

        cp.decide_scope("f", AgentTurn(1), Decision.REVERT)

        # The edit sat on turn 1's line, so reverting turn 1 drags it: back to v0.
        assert cp.view({"f": disk}).content("f") == _txt("a\nb\nc\n")

    def test_disjoint_manual_edit_survives_a_turn_revert(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "top\nb\n", "top\nB\n")  # turn 1 edits line 2
        disk = _txt("topX\nB\n")  # user edits line 1 (disjoint from turn 1)
        cp.reconcile("f", disk)

        cp.decide_scope("f", AgentTurn(1), Decision.REVERT)

        # Line 2 reverts to b; the disjoint line-1 edit is untouched.
        assert cp.view({"f": disk}).content("f") == _txt("topX\nb\n")

    def test_revert_is_a_ratchet_with_no_un_revert(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\nb\n", "A\nb\n")
        current = _txt("A\nb\n")
        rid = _view(cp, current)[0].region_id
        cp.decide_region("f", rid, Decision.REVERT)
        disk = cp.view({"f": current}).content("f")
        assert disk == _txt("a\nb\n")

        # Keeping a reverted hunk is a no-op: revert is terminal (no un-revert).
        cp.decide_region("f", rid, Decision.KEEP)

        assert cp.view({"f": disk}).content("f") == _txt("a\nb\n")
        view = {tr.region_id: tr for tr in _view(cp, disk)}
        assert view[rid].decision is Decision.REVERT


class TestRewindTruncation:
    def test_rewind_truncates_manual_edits_and_decisions_after_the_cut(self) -> None:
        cp = Checkpointer()
        _turn(cp, 1, "f", "a\n", "A\n")
        cp.reconcile("f", _txt("A1\n"))  # between-turn user edit
        _turn(cp, 2, "f", "A1\n", "A2\n")  # turn 2 builds on the user edit
        cp.decide_scope("f", AgentTurn(1), Decision.KEEP)  # decision after turn 2

        # Rewinding to turn 2 keeps turn 1 and the pre-turn-2 user edit, and drops
        # turn 2 plus the later decision.
        assert cp.view().restore_plan_to_turn(2) == {"f": _txt("A1\n")}
        cp.drop_turns_from(2)

        assert cp.view({"f": _txt("A1\n")}).content("f") == _txt("A1\n")
        remaining = cp.view({"f": _txt("A1\n")}).regions("f")
        assert all(tr.decision is Decision.PENDING for tr in remaining)
        assert {tr.owner for tr in remaining} == {AgentTurn(1), ManualEdit(1)}
