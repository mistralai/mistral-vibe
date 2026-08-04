from __future__ import annotations

from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest

from vibe.core.checkpoints import (
    AgentTurn,
    Checkpointer,
    CheckpointRecorder,
    Decision,
    FileSnapshot,
    FileState,
    OpaqueReason,
    Owner,
)
from vibe.core.review import (
    AllTarget,
    FileTarget,
    LastTurnsTarget,
    OpaqueReviewRegion,
    RegionsTarget,
    RegionTarget,
    ReviewError,
    ReviewFileStatus,
    ReviewManager,
    ScopeFileTarget,
    ScopeTarget,
    TextReviewRegion,
)
from vibe.core.types import LLMMessage, MessageList, Role


def _make_messages(*contents: str) -> MessageList:
    """Create a MessageList with a system message followed by user/assistant pairs."""
    msgs = MessageList([LLMMessage(role=Role.system, content="system")])
    for content in contents:
        msgs.append(LLMMessage(role=Role.user, content=content))
        msgs.append(LLMMessage(role=Role.assistant, content=f"reply to {content}"))
    return msgs


def _snap(path: Path) -> FileSnapshot:
    """Create a FileSnapshot by reading a file (or absent if missing)."""
    resolved = str(path.resolve())
    try:
        content: bytes | None = path.read_bytes()
    except FileNotFoundError:
        content = None
    return FileSnapshot(path=resolved, state=FileState(content))


class _Shells(NamedTuple):
    recorder: CheckpointRecorder
    review: ReviewManager
    messages: MessageList


def _shells(messages: MessageList) -> _Shells:
    checkpointer = Checkpointer()
    return _Shells(
        recorder=CheckpointRecorder(checkpointer, messages),
        review=ReviewManager(checkpointer),
        messages=messages,
    )


class _Turn:
    """Helper that simulates one conversation turn."""

    def __init__(self, sh: _Shells, messages: MessageList) -> None:
        self._mgr = sh
        self._messages = messages

    def begin(self, user_msg: str) -> None:
        """Start a new turn: create checkpoint then append user message.

        This mirrors agent_loop.act() which calls create_checkpoint()
        *before* the user message is added to the message list.
        """
        self._mgr.recorder.create_checkpoint()
        self._messages.append(LLMMessage(role=Role.user, content=user_msg))

    def tool_write(self, path: Path, content: str) -> None:
        """Simulate a tool writing to a file (snapshot → write)."""
        self._mgr.recorder.add_snapshot(_snap(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tool_delete(self, path: Path) -> None:
        """Simulate a tool deleting a file (snapshot → unlink)."""
        self._mgr.recorder.add_snapshot(_snap(path))
        path.unlink()

    def end(self, assistant_reply: str = "ok") -> None:
        """End the turn: seal the checkpoint (as agent_loop.act() does in its
        finally) then append the assistant reply.
        """
        self._mgr.recorder.seal_turn()
        self._messages.append(LLMMessage(role=Role.assistant, content=assistant_reply))


class TestReviewState:
    def test_review_state_reports_text_regions_and_status(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        modified = tmp_path / "modified.txt"
        created = tmp_path / "created.txt"
        modified.write_text("a\nb\nc\n", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(modified))
        sh.recorder.add_snapshot(
            FileSnapshot(path=str(created.resolve()), state=FileState(None))
        )
        modified.write_text("a\nB\nc\n", encoding="utf-8")
        created.write_text("new\n", encoding="utf-8")

        state = sh.review.review_state()

        assert [file.path for file in state.files] == [
            str(modified.resolve()),
            str(created.resolve()),
        ]
        assert state.files[0].status == ReviewFileStatus.MODIFIED
        region = state.files[0].regions[0]
        assert isinstance(region, TextReviewRegion)
        assert region.baseline_start == 1
        assert region.baseline_line_count == 1
        assert region.current_start == 1
        assert region.current_line_count == 1
        assert state.files[1].status == ReviewFileStatus.CREATED

    def test_baseline_text_returns_accepted_baseline(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        modified = tmp_path / "modified.txt"
        modified.write_text("before", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(modified))
        modified.write_text("after", encoding="utf-8")

        assert sh.review.baseline_text(str(modified.resolve())) == "before"

    def test_review_state_reports_deleted_and_binary_fallbacks(
        self, tmp_path: Path
    ) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        deleted = tmp_path / "deleted.txt"
        binary = tmp_path / "image.bin"
        deleted.write_text("gone", encoding="utf-8")
        binary.write_bytes(b"\x00old")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(deleted))
        sh.recorder.add_snapshot(_snap(binary))
        deleted.unlink()
        binary.write_bytes(b"\x00new")

        state = sh.review.review_state()

        assert [(file.path, file.status) for file in state.files] == [
            (str(deleted.resolve()), ReviewFileStatus.DELETED),
            (str(binary.resolve()), ReviewFileStatus.BINARY_OR_UNDECODABLE),
        ]
        # One opaque region per file (version_index is a stable edit seq, not
        # asserted here). Reason distinguishes deletion from binary.
        for file in state.files:
            assert len(file.regions) == 1
            region = file.regions[0]
            assert isinstance(region, OpaqueReviewRegion)
            assert region.ordinal == 0
            assert region.owner == AgentTurn(len(messages))
            assert region.decision is Decision.PENDING
            assert region.depends_on == ()
        missing, binary_region = state.files[0].regions[0], state.files[1].regions[0]
        assert isinstance(missing, OpaqueReviewRegion)
        assert isinstance(binary_region, OpaqueReviewRegion)
        assert missing.reason is OpaqueReason.MISSING
        assert binary_region.reason is OpaqueReason.BINARY_OR_UNDECODABLE


class TestReviewMutations:
    @staticmethod
    def _one_turn(sh: _Shells, path: Path, pre: str | None, post: str | None) -> str:
        if pre is not None:
            path.write_text(pre, encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(path))
        if post is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(post, encoding="utf-8")
        sh.recorder.seal_turn()
        return str(path.resolve())

    def test_revert_file_restores_baseline_on_disk(self, tmp_path: Path) -> None:
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        resolved = self._one_turn(sh, f, "a\nb\nc\n", "a\nB\nc\n")

        sh.review.revert_review(FileTarget(path=resolved))

        assert f.read_text(encoding="utf-8") == "a\nb\nc\n"
        assert sh.review.review_state().files == []

    def test_approve_file_keeps_disk_and_clears_pending(self, tmp_path: Path) -> None:
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        resolved = self._one_turn(sh, f, "a\nb\n", "A\nb\n")

        sh.review.approve_review(FileTarget(path=resolved))

        assert f.read_text(encoding="utf-8") == "A\nb\n"
        assert sh.review.review_state().files == []

    def test_revert_region_persists_only_that_hunk(self, tmp_path: Path) -> None:
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        resolved = self._one_turn(sh, f, "1\nx\n2\n", "A\nx\nB\n")

        first_region = sh.review.review_state().files[0].regions[0]
        sh.review.revert_review(
            RegionTarget(
                path=resolved,
                version_index=first_region.version_index,
                ordinal=first_region.ordinal,
            )
        )

        assert f.read_text(encoding="utf-8") == "1\nx\nB\n"

    def test_approve_region_clears_only_that_hunk_and_keeps_disk(
        self, tmp_path: Path
    ) -> None:
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        resolved = self._one_turn(sh, f, "1\nx\n2\n", "A\nx\nB\n")

        first_region = sh.review.review_state().files[0].regions[0]
        sh.review.approve_review(
            RegionTarget(
                path=resolved,
                version_index=first_region.version_index,
                ordinal=first_region.ordinal,
            )
        )

        # Both hunks remain visible; the approved one is now KEEP, the other
        # still PENDING. Approving leaves disk untouched.
        regions = sh.review.review_state().files[0].regions
        by_ordinal = {r.ordinal: r.decision for r in regions}
        assert by_ordinal[first_region.ordinal] is Decision.KEEP
        assert any(d is Decision.PENDING for d in by_ordinal.values())
        assert f.read_text(encoding="utf-8") == "A\nx\nB\n"

    def test_revert_all_restores_every_file(self, tmp_path: Path) -> None:
        sh = _shells(_make_messages("m"))
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"
        f1.write_text("a\n", encoding="utf-8")
        f2.write_text("b\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f1))
        sh.recorder.add_snapshot(_snap(f2))
        f1.write_text("A\n", encoding="utf-8")
        f2.write_text("B\n", encoding="utf-8")
        sh.recorder.seal_turn()

        sh.review.revert_review(AllTarget())

        assert f1.read_text(encoding="utf-8") == "a\n"
        assert f2.read_text(encoding="utf-8") == "b\n"
        assert sh.review.review_state().files == []

    def test_all_target_resolves_pending_when_disk_matches_original(
        self, tmp_path: Path
    ) -> None:
        # Agent changes a -> b, then a manual edit puts it back to a. Disk equals
        # the original, but two pending regions remain; the whole-review approve
        # must resolve them rather than no-op.
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        resolved = self._one_turn(sh, f, "a\n", "b\n")
        f.write_text("a\n", encoding="utf-8")

        assert any(
            r.decision is Decision.PENDING
            for file in sh.review.review_state().files
            for r in file.regions
        )

        affected = sh.review.approve_review(AllTarget())

        assert resolved in affected
        assert sh.review.review_state().files == []

    def test_failed_persist_rolls_back_decision(self, tmp_path: Path) -> None:
        # If writing reverted content to disk fails, the revert decision must not
        # be committed: the hunk stays pending for a later retry and disk is left
        # untouched.
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        resolved = self._one_turn(sh, f, "a\nb\nc\n", "a\nB\nc\n")

        with (
            patch.object(sh.review._files, "apply", return_value=(["boom"], [])),
            pytest.raises(ReviewError),
        ):
            sh.review.revert_review(FileTarget(path=resolved))

        regions = sh.review.review_state().files[0].regions
        assert all(r.decision is Decision.PENDING for r in regions)
        assert f.read_text(encoding="utf-8") == "a\nB\nc\n"

    def test_revert_created_file_deletes_it(self, tmp_path: Path) -> None:
        sh = _shells(_make_messages("m"))
        f = tmp_path / "new.txt"
        resolved = self._one_turn(sh, f, None, "created\n")

        sh.review.revert_review(FileTarget(path=resolved))

        assert not f.exists()

    def test_revert_last_turns_scopes_to_recent_turn(self, tmp_path: Path) -> None:
        messages = _make_messages("m1")
        sh = _shells(messages)
        f1 = tmp_path / "f1.txt"
        f2 = tmp_path / "f2.txt"

        f1.write_text("a\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f1))
        f1.write_text("A\n", encoding="utf-8")
        sh.recorder.seal_turn()
        sh.review.approve_review(FileTarget(path=str(f1.resolve())))

        messages.append(LLMMessage(role=Role.user, content="m2"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f2))
        f2.write_text("b\n", encoding="utf-8")
        sh.recorder.seal_turn()

        sh.review.revert_review(LastTurnsTarget(count=1))

        assert not f2.exists()
        assert f1.read_text(encoding="utf-8") == "A\n"


class TestTurnFileDiff:
    def test_scope_file_diff_shows_each_turns_own_change(self, tmp_path: Path) -> None:
        messages = _make_messages("m1")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        path = str(f.resolve())

        f.write_text("a\nb\nc\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("A\nb\nc\n", encoding="utf-8")
        sh.recorder.seal_turn()

        messages.append(LLMMessage(role=Role.user, content="m2"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("A\nb\nC\n", encoding="utf-8")
        sh.recorder.seal_turn()

        first, second = (turn.owner for turn in sh.review.review_state().scopes)

        first_diff = sh.review.scope_file_diff(path, first)
        assert first_diff.status is ReviewFileStatus.MODIFIED
        assert first_diff.baseline == "a\nb\nc\n"
        assert first_diff.current == "A\nb\nc\n"

        second_diff = sh.review.scope_file_diff(path, second)
        assert second_diff.baseline == "A\nb\nc\n"
        assert second_diff.current == "A\nb\nC\n"

    def test_scope_file_diff_reports_created_status(self, tmp_path: Path) -> None:
        messages = _make_messages("m1")
        sh = _shells(messages)
        f = tmp_path / "new.txt"
        path = str(f.resolve())

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("hello\n", encoding="utf-8")
        sh.recorder.seal_turn()

        turn_id = sh.review.review_state().scopes[0].owner
        diff = sh.review.scope_file_diff(path, turn_id)

        assert diff.status is ReviewFileStatus.CREATED
        assert diff.baseline == ""
        assert diff.current == "hello\n"

    def test_file_hunks_anchor_pending_changes(self, tmp_path: Path) -> None:
        messages = _make_messages("m1")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        path = str(f.resolve())

        f.write_text("a\nb\nc\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("A\nb\nc\n", encoding="utf-8")
        sh.recorder.seal_turn()

        messages.append(LLMMessage(role=Role.user, content="m2"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("A\nb\nC\n", encoding="utf-8")
        sh.recorder.seal_turn()

        # Whole-file view: both edits anchored on the additions side.
        whole = sh.review.file_hunks(path)
        assert [(h.side, h.line) for h in whole] == [("additions", 0), ("additions", 2)]
        assert all(len(h.regions) >= 1 for h in whole)

        # Scope view: only the selected turn's own edit.
        _first, second = (turn.owner for turn in sh.review.review_state().scopes)
        scoped = sh.review.file_hunks(path, second)
        assert [(h.side, h.line) for h in scoped] == [("additions", 2)]

    def test_deciding_a_turn_keeps_its_slot_and_empties_its_diff(
        self, tmp_path: Path
    ) -> None:
        messages = _make_messages("m1")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        path = str(f.resolve())

        f.write_text("a\nb\nc\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("A\nb\nc\n", encoding="utf-8")
        sh.recorder.seal_turn()

        messages.append(LLMMessage(role=Role.user, content="m2"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("A\nb\nC\n", encoding="utf-8")
        sh.recorder.seal_turn()

        first, second = (turn.owner for turn in sh.review.review_state().scopes)
        sh.review.approve_review(ScopeTarget(owner=first))

        # The decided turn keeps its stable slot (so numbering never shifts) but
        # has nothing left to review; only the pending turn still carries files.
        turns = sh.review.review_state().scopes
        assert [turn.owner for turn in turns] == [first, second]
        decided_turn = next(turn for turn in turns if turn.owner == first)
        assert decided_turn.files == []
        decided = sh.review.scope_file_diff(path, first)
        assert decided.baseline == ""
        assert decided.current == ""


class TestTurnFileAndRegionsTargets:
    @staticmethod
    def _two_turns_same_file(sh: _Shells, f: Path) -> tuple[Owner, Owner]:
        path = str(f.resolve())
        f.write_text("a\nb\nc\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(FileSnapshot(path=path, state=FileState(b"a\nb\nc\n")))
        f.write_text("A\nb\nc\n", encoding="utf-8")
        sh.recorder.seal_turn()

        sh.messages.append(LLMMessage(role=Role.user, content="m2"))
        sh.messages.append(LLMMessage(role=Role.assistant, content="r"))
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(FileSnapshot(path=path, state=FileState(b"A\nb\nc\n")))
        f.write_text("A\nb\nC\n", encoding="utf-8")
        sh.recorder.seal_turn()
        scopes = sh.review.review_state().scopes
        return scopes[0].owner, scopes[1].owner

    def test_turn_file_revert_scopes_to_that_turn(self, tmp_path: Path) -> None:
        messages = _make_messages("m1")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        first, _second = self._two_turns_same_file(sh, f)

        sh.review.revert_review(ScopeFileTarget(owner=first, path=str(f.resolve())))

        # Turn 1's line reverts; turn 2's independent line stays.
        assert f.read_text(encoding="utf-8") == "a\nb\nC\n"

    def test_regions_target_decides_every_listed_hunk(self, tmp_path: Path) -> None:
        messages = _make_messages("m1")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        path = str(f.resolve())

        f.write_text("a\nb\nc\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(FileSnapshot(path=path, state=FileState(b"a\nb\nc\n")))
        f.write_text("A\nb\nC\n", encoding="utf-8")  # one turn, two hunks
        sh.recorder.seal_turn()

        regions = sh.review.review_state().files[0].regions
        refs = tuple((r.version_index, r.ordinal) for r in regions)
        sh.review.revert_review(RegionsTarget(path=path, regions=refs))

        assert f.read_text(encoding="utf-8") == "a\nb\nc\n"
        assert sh.review.review_state().files == []


class TestManualEditDuringReview:
    """Manual edits made while a review is in progress must be preserved by
    unrelated decisions on other turns/zones.
    """

    @staticmethod
    def _sfile(
        top: str = "top",
        a: str = "A_0",
        b: str = "B_0",
        c: str = "C_0",
        d: tuple[str, ...] = ("D_0",),
        e: str = "E_0",
        bot: str = "bot",
    ) -> str:
        # Anchor lines ("--a--" ..) keep each editable zone's hunks separate.
        lines = [top, a, "--a--", b, "--b--", c, "--c--", *d, "--d--", e, bot]
        return "\n".join(lines) + "\n"

    def test_mid_review_manual_edit_survives_a_later_turn_revert(
        self, tmp_path: Path
    ) -> None:
        messages = MessageList([LLMMessage(role=Role.system, content="system")])
        sh = _shells(messages)
        turn = _Turn(sh, messages)
        f = tmp_path / "s.txt"
        s = self._sfile
        f.write_text(s(), encoding="utf-8")

        # Five agent turns, each preceded (from t2 on) by a between-turns user edit
        # that overlaps the turn's own zone. Mirrors the e2e 5-turn scenario.
        turn.begin("t1")
        turn.tool_write(f, s(a="A_1"))
        turn.end()

        f.write_text(s(a="A_1", b="B_u"), encoding="utf-8")
        turn.begin("t2")
        turn.tool_write(f, s(a="A_1", b="B_2", c="C_2"))
        turn.end()

        f.write_text(s(a="A_1", b="B_2", c="C_u"), encoding="utf-8")
        turn.begin("t3")
        turn.tool_write(f, s(a="A_1", b="B_2", c="C_3", d=("D_0", "D_i1", "D_i2")))
        turn.end()

        f.write_text(
            s(a="A_1", b="B_2", c="C_3", d=("D_0", "D_i1", "D_i2"), e="E_u"),
            encoding="utf-8",
        )
        turn.begin("t4")
        turn.tool_write(
            f, s(a="A_4", b="B_2", c="C_3", d=("D_0", "D_i1", "D_i2"), e="E_4")
        )
        turn.end()

        f.write_text(
            s(a="A_4", b="B_2", c="C_3", d=("D_0", "D_iU", "D_i2"), e="E_4"),
            encoding="utf-8",
        )
        turn.begin("t5")
        turn.tool_write(
            f, s(a="A_4", b="B_2", c="C_3", d=("D_0", "D_5", "D_i2"), e="E_5")
        )
        turn.end()

        t1, t2, t3, t4, t5 = (
            s.owner
            for s in sh.review.review_state().scopes
            if isinstance(s.owner, AgentTurn)
        )

        # Revert the two newest turns first (each persists to disk).
        sh.review.revert_review(ScopeTarget(owner=t5))
        sh.review.revert_review(ScopeTarget(owner=t4))

        # Manual edit mid-review on the untouched top line.
        current = f.read_text(encoding="utf-8")
        f.write_text(current.replace("top\n", "topx\n", 1), encoding="utf-8")

        # Reverting an unrelated turn (t3 touches C and D) must not clobber the
        # top-line manual edit.
        sh.review.revert_review(ScopeTarget(owner=t3))

        assert f.read_text(encoding="utf-8") == s(
            top="topx", a="A_1", b="B_2", c="C_u", d=("D_0",), e="E_u"
        )


class TestReviewErrors:
    def test_decision_while_turn_open_raises(self, tmp_path: Path) -> None:
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        f.write_text("a\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("b\n", encoding="utf-8")

        # The turn is left open (never sealed): a review decision is refused
        # rather than racing the in-flight turn.
        with pytest.raises(ReviewError):
            sh.review.revert_review(FileTarget(path=str(f.resolve())))

    def test_write_failure_raises(self, tmp_path: Path) -> None:
        sh = _shells(_make_messages("m"))
        f = tmp_path / "f.txt"
        f.write_text("a\nb\nc\n", encoding="utf-8")
        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("a\nB\nc\n", encoding="utf-8")
        sh.recorder.seal_turn()

        with patch.object(sh.review._files, "apply", return_value=(["disk full"], [])):
            with pytest.raises(ReviewError, match="disk full"):
                sh.review.revert_review(FileTarget(path=str(f.resolve())))
