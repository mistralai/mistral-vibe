"""Unit coverage for the unified backend's rewind file snapshots."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from vibe.app_server._effect_models import (
    FileEditEffectDetail,
    FileEditEffectInput,
    FileEditEffectOccurrence,
    FileEditEffectOutput,
    FileWriteEffectDetail,
    FileWriteEffectInput,
    FileWriteEffectOutput,
)
from vibe.app_server._unified_harness_snapshots import RewindFileSnapshots
from vibe.app_server._unified_tool_projection import project_unified_history_entry
from vibe.app_server.models import (
    BlockedSessionStatus,
    CompletedEffectState,
    IdleSessionStatus,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicSession,
    PublicSessionState,
    RunningSessionStatus,
    TextContentBlock,
    validate_history_entry,
)
from vibe.core.checkpoints import FileState, FileStore
from vibe.utils.tool_presentation import EffectCallDisplay, EffectResultDisplay

_NOW = 1


def _anchor(turn: str) -> str:
    return f"user-{turn}"


def _user_entry(turn: str) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=_anchor(turn),
        session_id="session-1",
        turn_id=turn,
        created_at=_NOW,
        updated_at=_NOW,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=turn)],
    )


def _state(
    active: str | None,
    entries: Sequence[PublicHistoryEntry] = (),
    *,
    status: Any = None,
) -> PublicSessionState:
    if status is None:
        status = (
            RunningSessionStatus(active_turn_id=active)
            if active
            else IdleSessionStatus()
        )
    history: list[PublicHistoryEntry] = [_user_entry(active)] if active else []
    return PublicSessionState(
        event_id=_NOW,
        session=PublicSession(
            id="session-1", status=status, created_at=_NOW, updated_at=_NOW
        ),
        history=[*history, *entries],
    )


def _write_effect(
    path: Path,
    *,
    turn: str,
    content: str,
    replaced: str | None = None,
    existed: bool = True,
) -> PublicEffectEntry:
    output = FileWriteEffectOutput(
        file_path=str(path),
        content=content,
        file_existed=existed,
        previous_content=replaced,
    )
    return PublicEffectEntry(
        id=f"write-{turn}-{path.name}",
        session_id="session-1",
        turn_id=turn,
        created_at=_NOW,
        updated_at=_NOW,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="write_file",
        detail=FileWriteEffectDetail(
            tool_name="write_file",
            display=EffectCallDisplay(summary="Writing", status_text="Writing"),
            input=FileWriteEffectInput(file_path=str(path), content=content),
        ),
        state=CompletedEffectState(
            output=(
                output.model_dump(mode="json", by_alias=True)
                if output is not None
                else None
            ),
            display=EffectResultDisplay(success=True, message=str(path)),
        ),
    )


def _edit_effect(
    path: Path,
    *,
    turn: str,
    old_string: str = "a",
    new_string: str = "b",
    start_line: int | None = 1,
    new_start_line: int | None = None,
    previous_content: str | None = None,
    with_occurrences: bool = True,
    occurrences: list[tuple[int, int | None, str, str]] | None = None,
) -> PublicEffectEntry:
    # One hunk per edit unless the caller supplies the harness's full batch.
    hunks = (
        occurrences
        if occurrences is not None
        else [(start_line, new_start_line, old_string, new_string)]
    )
    output = (
        FileEditEffectOutput(
            file=str(path),
            occurrences=[
                FileEditEffectOccurrence(
                    start_line=hunk_start,
                    new_start_line=hunk_new_start,
                    old_text=hunk_old,
                    new_text=hunk_new,
                )
                for hunk_start, hunk_new_start, hunk_old, hunk_new in hunks
            ],
            previous_content=previous_content,
        )
        if with_occurrences
        else None
    )
    return PublicEffectEntry(
        id=f"edit-{turn}-{path.name}",
        session_id="session-1",
        turn_id=turn,
        created_at=_NOW,
        updated_at=_NOW,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="edit",
        detail=FileEditEffectDetail(
            tool_name="edit",
            display=EffectCallDisplay(summary="Editing", status_text="Editing"),
            input=FileEditEffectInput(
                file_path=str(path), old_string=old_string, new_string=new_string
            ),
        ),
        state=CompletedEffectState(
            output=(
                output.model_dump(mode="json", by_alias=True)
                if output is not None
                else None
            ),
            display=EffectResultDisplay(success=True, message=str(path)),
        ),
    )


class _UnreadableFiles(FileStore):
    """A file store that fails to read the paths marked unreadable."""

    def __init__(self) -> None:
        super().__init__()
        self.unreadable: set[str] = set()

    def read(self, path: str) -> FileState:
        if path in self.unreadable:
            raise PermissionError(path)
        return super().read(path)


def _recorder(cwd: Path) -> RewindFileSnapshots:
    recorder = RewindFileSnapshots(cwd=lambda: str(cwd))
    recorder.observe(_state(None))
    return recorder


def _write(path: Path, content: str) -> Callable[[], None]:
    def write() -> None:
        path.write_text(content, encoding="utf-8")

    return write


def _run(
    recorder: RewindFileSnapshots,
    turn: str,
    effects: Sequence[PublicEffectEntry] = (),
    *,
    mid_turn: Callable[[], None] | None = None,
) -> None:
    """One turn: it starts, its tools write, their effects land, it settles."""
    recorder.observe(_state(turn))
    if mid_turn is not None:
        mid_turn()
    recorder.observe(_state(turn, effects))
    recorder.observe(_state(None))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestRestore:
    def test_a_created_file_is_deleted(self, tmp_path: Path) -> None:
        target = tmp_path / "new.txt"
        recorder = _recorder(tmp_path)
        effect = _write_effect(target, turn="t1", content="created", existed=False)
        _run(recorder, "t1", [effect], mid_turn=_write(target, "created"))

        assert recorder.restorable_paths(_anchor("t1")) == [str(target.resolve())]
        errors, restored = recorder.restore(_anchor("t1"))

        assert errors == []
        assert restored == [str(target.resolve())]
        assert not target.exists()

    def test_an_overwritten_file_gets_its_replaced_text_back(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "config.yaml"
        target.write_text("original", encoding="utf-8")
        recorder = _recorder(tmp_path)
        effect = _write_effect(target, turn="t1", content="new", replaced="original")
        _run(recorder, "t1", [effect], mid_turn=_write(target, "new"))

        recorder.restore(_anchor("t1"))

        assert _text(target) == "original"

    def test_a_later_turn_restores_to_the_earlier_turns_result(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "app.py"
        target.write_text("v0", encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _write_effect(target, turn="t1", content="v1", replaced="v0")
        _run(recorder, "t1", [first], mid_turn=_write(target, "v1"))
        second = _write_effect(target, turn="t2", content="v2", replaced="v1")
        _run(recorder, "t2", [second], mid_turn=_write(target, "v2"))

        recorder.restore(_anchor("t2"))

        assert _text(target) == "v1"

    def test_a_shell_write_on_a_carried_path_is_restored(self, tmp_path: Path) -> None:
        target = tmp_path / "app.py"
        target.write_text("v0", encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _write_effect(target, turn="t1", content="v1", replaced="v0")
        _run(recorder, "t1", [first], mid_turn=_write(target, "v1"))
        # A shell command reports no file effect; the carried read covers it.
        _run(recorder, "t2", mid_turn=_write(target, "shell wrote this"))
        _run(recorder, "t3")

        assert recorder.restorable_paths(_anchor("t3")) == []
        recorder.restore(_anchor("t2"))
        assert _text(target) == "v1"

    def test_a_write_overrides_a_begin_read_that_lagged_it(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "app.py"
        target.write_text("v0", encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _write_effect(target, turn="t1", content="v1", replaced="v0")
        _run(recorder, "t1", [first], mid_turn=_write(target, "v1"))
        # The runtime already wrote v2 when the adapter translates the start.
        target.write_text("v2", encoding="utf-8")
        second = _write_effect(target, turn="t2", content="v2", replaced="v1")
        _run(recorder, "t2", [second])

        recorder.restore(_anchor("t2"))

        assert _text(target) == "v1"

    def test_an_edit_first_touch_is_tracked_from_there(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.md"
        target.write_text("before", encoding="utf-8")
        recorder = _recorder(tmp_path)
        _run(
            recorder,
            "t1",
            [_edit_effect(target, turn="t1", with_occurrences=False)],
            mid_turn=_write(target, "edited"),
        )
        assert recorder.restorable_paths(_anchor("t1")) == []

        second = _write_effect(
            target, turn="t2", content="rewritten", replaced="edited"
        )
        _run(recorder, "t2", [second], mid_turn=_write(target, "rewritten"))
        recorder.restore(_anchor("t2"))

        assert _text(target) == "edited"

    def test_an_edit_effect_reconstructs_the_preedit_state(
        self, tmp_path: Path
    ) -> None:
        """The runtime applies an edit before its effect is observable, so a
        raw disk read at effect time records post-edit content. The effect's
        own old/new text must recover the true turn-start state.
        """
        target = tmp_path / "alpha.txt"
        original = "one\ntwo\nthree\n"
        target.write_text(original, encoding="utf-8")
        recorder = _recorder(tmp_path)
        _run(
            recorder,
            "t1",
            [
                _edit_effect(
                    target,
                    turn="t1",
                    old_string="two\n",
                    new_string="TWO\n",
                    start_line=2,
                )
            ],
            mid_turn=_write(target, "one\nTWO\nthree\n"),
        )

        assert recorder.restorable_paths(_anchor("t1")) == [str(target)]
        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_a_later_edit_effect_refines_the_reconstruction(
        self, tmp_path: Path
    ) -> None:
        """Two edits land before either effect is observed: the first
        observation reconstructs from the effects it has seen so far, and the
        second effect's arrival must refine the recorded pre-edit state to the
        turn's true start content.
        """
        target = tmp_path / "alpha.txt"
        original = "one\ntwo\nthree\nfour\nfive\nsix\n"
        final = "one\nTWO\nthree\nfour\nFIVE\nsix\n"
        target.write_text(original, encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _edit_effect(
            target, turn="t1", old_string="two\n", new_string="TWO\n", start_line=2
        )
        second = _edit_effect(
            target, turn="t1", old_string="five\n", new_string="FIVE\n", start_line=5
        )
        second = second.model_copy(update={"id": "edit-t1-alpha-2"})

        recorder.observe(_state("t1"))
        _write(target, final)()
        recorder.observe(_state("t1", [first]))
        recorder.observe(_state("t1", [first, second]))
        recorder.observe(_state(None))

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_occurrence_text_without_endings_reconstructs_cleanly(
        self, tmp_path: Path
    ) -> None:
        """The runtime's occurrence old/new text carries line content without
        trailing newlines; the reconstruction must reattach the replaced
        block's endings instead of splicing the bare text as whole lines.
        """
        target = tmp_path / "svc.txt"
        original = "header\nkeepA\nbbb\nkeepB\nccc\nkeepC\neee\nfooter\n"
        target.write_text(original, encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _edit_effect(
            target, turn="t1", old_string="bbb", new_string="BBB_T1", start_line=3
        )
        second = _edit_effect(
            target, turn="t1", old_string="eee", new_string="EEE_T1", start_line=7
        )
        second = second.model_copy(update={"id": "edit-t1-svc-2"})

        recorder.observe(_state("t1"))
        _write(target, "header\nkeepA\nBBB_T1\nkeepB\nccc\nkeepC\nEEE_T1\nfooter\n")()
        recorder.observe(_state("t1", [first]))
        recorder.observe(_state("t1", [first, second]))
        recorder.observe(_state(None))

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_a_deletion_hunk_is_skipped_rather_than_spliced(
        self, tmp_path: Path
    ) -> None:
        """A deletion leaves no post-edit text to verify the hunk against,
        so the fallback skips it rather than splicing a corrupted file.
        """
        target = tmp_path / "alpha.txt"
        target.write_text("one\ntwo\nthree\n", encoding="utf-8")
        recorder = _recorder(tmp_path)
        _run(
            recorder,
            "t1",
            [
                _edit_effect(
                    target, turn="t1", old_string="two\n", new_string="", start_line=2
                )
            ],
            mid_turn=_write(target, "one\nthree\n"),
        )

        assert recorder.restorable_paths(_anchor("t1")) == []
        recorder.restore(_anchor("t1"))
        assert _text(target) == "one\nthree\n"

    def test_a_deletion_with_carried_text_restores_exactly(
        self, tmp_path: Path
    ) -> None:
        """The carried pre-edit text is exact for deletions too: rewind undoes
        them even though the fallback cannot verify a deletion hunk.
        """
        target = tmp_path / "alpha.txt"
        original = "one\ntwo\nthree\n"
        target.write_text(original, encoding="utf-8")
        recorder = _recorder(tmp_path)
        _run(
            recorder,
            "t1",
            [
                _edit_effect(
                    target,
                    turn="t1",
                    old_string="two\n",
                    new_string="",
                    start_line=2,
                    previous_content=original,
                )
            ],
            mid_turn=_write(target, "one\nthree\n"),
        )

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_a_write_after_edits_refines_through_its_replaced_text(
        self, tmp_path: Path
    ) -> None:
        """A write's replaced text is an exact mid-turn state: reversing the
        edits observed before it against that state yields the turn-start
        content even though the current disk has moved on past the write.
        """
        target = tmp_path / "alpha.txt"
        original = "one\ntwo\nthree\n"
        target.write_text(original, encoding="utf-8")
        recorder = _recorder(tmp_path)
        edit = _edit_effect(
            target, turn="t1", old_string="two\n", new_string="TWO\n", start_line=2
        )
        write = _write_effect(
            target, turn="t1", content="rewritten", replaced="one\nTWO\nthree\n"
        )
        write = write.model_copy(update={"id": f"write-t1-{target.name}"})

        recorder.observe(_state("t1"))
        _write(target, "rewritten")()
        recorder.observe(_state("t1", [edit]))
        recorder.observe(_state("t1", [edit, write]))
        recorder.observe(_state(None))

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_a_write_the_runtime_could_not_diff_keeps_the_edit_snapshot(
        self, tmp_path: Path
    ) -> None:
        """A write with no replaced text records nothing for the path, so it
        must not trigger a refinement: re-reading the post-write disk would
        reverse the earlier edits against content they never touched and
        clobber the already-correct turn-start snapshot with a worse one.
        """
        target = tmp_path / "alpha.txt"
        original = "one\ntwo\nthree\n"
        target.write_text(original, encoding="utf-8")
        recorder = _recorder(tmp_path)
        edit = _edit_effect(
            target, turn="t1", old_string="two\n", new_string="TWO\n", start_line=2
        )
        write = _write_effect(target, turn="t1", content="rewritten", replaced=None)
        write = write.model_copy(update={"id": f"write-t1-{target.name}"})

        recorder.observe(_state("t1"))
        _write(target, "one\nTWO\nthree\n")()
        recorder.observe(_state("t1", [edit]))
        _write(target, "rewritten")()
        recorder.observe(_state("t1", [edit, write]))
        recorder.observe(_state(None))

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    @pytest.mark.parametrize("anchor", ["user-never-observed", ""])
    def test_an_unrecorded_anchor_restores_nothing(
        self, tmp_path: Path, anchor: str
    ) -> None:
        target = tmp_path / "kept.txt"
        target.write_text("old", encoding="utf-8")
        recorder = _recorder(tmp_path)
        effect = _write_effect(target, turn="t2", content="new", replaced="old")
        _run(recorder, "t2", [effect], mid_turn=_write(target, "new"))

        assert recorder.restorable_paths(anchor) == []
        assert recorder.restore(anchor) == ([], [])
        assert _text(target) == "new"

    def test_an_unreadable_file_is_reported_and_the_rest_restored(
        self, tmp_path: Path
    ) -> None:
        locked = tmp_path / "locked.txt"
        locked.write_text("old", encoding="utf-8")
        created = tmp_path / "created.txt"
        files = _UnreadableFiles()
        recorder = RewindFileSnapshots(cwd=lambda: str(tmp_path), files=files)
        recorder.observe(_state(None))
        effects = [
            _write_effect(locked, turn="t1", content="new", replaced="old"),
            _write_effect(created, turn="t1", content="created", existed=False),
        ]

        def write_both() -> None:
            locked.write_text("new", encoding="utf-8")
            created.write_text("created", encoding="utf-8")

        _run(recorder, "t1", effects, mid_turn=write_both)
        files.unreadable.add(str(locked.resolve()))

        assert recorder.restorable_paths(_anchor("t1")) == [str(created.resolve())]
        errors, restored = recorder.restore(_anchor("t1"))

        assert errors == [f"Failed to read file: {locked.resolve()}"]
        assert restored == [str(created.resolve())]
        assert _text(locked) == "new"
        assert not created.exists()


class TestReadsDuringATurn:
    def test_checking_for_changes_mid_turn_keeps_recording_it(
        self, tmp_path: Path
    ) -> None:
        early = tmp_path / "early.txt"
        late = tmp_path / "late.txt"
        recorder = _recorder(tmp_path)
        recorder.observe(_state("t1"))
        early.write_text("early", encoding="utf-8")
        early_effect = _write_effect(early, turn="t1", content="early", existed=False)
        recorder.observe(_state("t1", [early_effect]))

        assert recorder.restorable_paths(_anchor("t1")) == [str(early.resolve())]

        late.write_text("late", encoding="utf-8")
        late_effect = _write_effect(late, turn="t1", content="late", existed=False)
        recorder.observe(_state("t1", [early_effect, late_effect]))
        recorder.observe(_state(None))
        recorder.restore(_anchor("t1"))

        assert not early.exists()
        assert not late.exists()


class TestDrop:
    def test_dropping_forgets_the_truncated_turns(self, tmp_path: Path) -> None:
        target = tmp_path / "created.txt"
        recorder = _recorder(tmp_path)
        effect = _write_effect(target, turn="t1", content="created", existed=False)
        _run(recorder, "t1", [effect], mid_turn=_write(target, "created"))

        recorder.drop_from(_anchor("t1"))

        assert _text(target) == "created"
        assert recorder.restorable_paths(_anchor("t1")) == []

    def test_dropping_keeps_the_turns_before_the_cut(self, tmp_path: Path) -> None:
        target = tmp_path / "app.py"
        target.write_text("v0", encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _write_effect(target, turn="t1", content="v1", replaced="v0")
        _run(recorder, "t1", [first], mid_turn=_write(target, "v1"))
        _run(recorder, "t2")

        recorder.drop_from(_anchor("t2"))
        recorder.restore(_anchor("t1"))

        assert _text(target) == "v0"

    def test_dropping_from_an_unrecorded_anchor_forgets_everything(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "created.txt"
        recorder = _recorder(tmp_path)
        effect = _write_effect(target, turn="t1", content="created", existed=False)
        _run(recorder, "t1", [effect], mid_turn=_write(target, "created"))

        recorder.drop_from("user-before-this-process")

        assert recorder.restorable_paths(_anchor("t1")) == []

    def test_dropping_a_turn_still_open_lets_the_next_turn_record(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "next.txt"
        recorder = _recorder(tmp_path)
        # The rewind lands before the adapter translated the turn's settling.
        recorder.observe(_state("t1"))
        recorder.drop_from(_anchor("t1"))
        recorder.observe(_state(None))

        effect = _write_effect(target, turn="t2", content="next", existed=False)
        _run(recorder, "t2", [effect], mid_turn=_write(target, "next"))

        assert recorder.restorable_paths(_anchor("t2")) == [str(target.resolve())]


class TestFork:
    def test_a_fork_inherits_the_kept_turns_under_its_own_ids(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "app.py"
        target.write_text("v0", encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _write_effect(target, turn="t1", content="v1", replaced="v0")
        _run(recorder, "t1", [first], mid_turn=_write(target, "v1"))
        second = _write_effect(target, turn="t2", content="v2", replaced="v1")
        _run(recorder, "t2", [second], mid_turn=_write(target, "v2"))

        forked = recorder.forked(_anchor("t2"), {_anchor("t1"): "child-1"})

        assert forked.restorable_paths(_anchor("t2")) == []
        assert forked.restorable_paths(_anchor("t1")) == []
        forked.restore("child-1")
        assert _text(target) == "v0"
        # The source keeps every turn.
        assert recorder.restorable_paths(_anchor("t2")) == [str(target.resolve())]

    def test_a_forks_first_turn_carries_the_inherited_paths(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "app.py"
        target.write_text("v0", encoding="utf-8")
        recorder = _recorder(tmp_path)
        first = _write_effect(target, turn="t1", content="v1", replaced="v0")
        _run(recorder, "t1", [first], mid_turn=_write(target, "v1"))
        second = _write_effect(target, turn="t2", content="v2", replaced="v1")
        _run(recorder, "t2", [second], mid_turn=_write(target, "v2"))
        recorder.restore(_anchor("t2"))

        forked = recorder.forked(_anchor("t2"), {_anchor("t1"): "child-1"})
        # The fork's first observation is its own next turn already running.
        forked.observe(_state("c2"))
        target.write_text("shell wrote this", encoding="utf-8")
        forked.observe(_state(None))

        forked.restore(_anchor("c2"))
        assert _text(target) == "v1"

    def test_an_inherited_write_does_not_hide_the_next_turns_first_write(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "app.py"
        target.write_text("v1", encoding="utf-8")
        recorder = _recorder(tmp_path)
        # A fork republishes inherited entries with no turn id.
        inherited = _write_effect(
            target, turn="t0", content="v1", replaced="v0"
        ).model_copy(update={"turn_id": None})
        recorder.observe(_state(None, [inherited]))
        second = _write_effect(target, turn="t1", content="v2", replaced="v1")
        _run(recorder, "t1", [inherited, second], mid_turn=_write(target, "v2"))

        recorder.restore(_anchor("t1"))

        assert _text(target) == "v1"


class TestTurnBoundaries:
    def test_a_first_observation_mid_turn_joins_the_running_turn(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "mid.txt"
        target.write_text("mid-turn write", encoding="utf-8")
        recorder = RewindFileSnapshots(cwd=lambda: str(tmp_path))
        effect = _write_effect(
            target, turn="t1", content="mid-turn write", existed=False
        )

        recorder.observe(_state("t1", [effect]))
        recorder.observe(_state(None))

        assert recorder.restorable_paths(_anchor("t1")) == [str(target.resolve())]

    def test_a_blocked_turn_stays_the_same_turn(self, tmp_path: Path) -> None:
        target = tmp_path / "blocked.txt"
        recorder = _recorder(tmp_path)
        recorder.observe(_state("t1"))
        blocked = BlockedSessionStatus(
            active_turn_id="t1", callback_id="cb-1", reason="approval"
        )
        recorder.observe(_state("t1", status=blocked))
        target.write_text("written", encoding="utf-8")
        effect = _write_effect(target, turn="t1", content="written", existed=False)
        recorder.observe(_state("t1", [effect]))
        recorder.observe(_state(None))

        assert recorder.restorable_paths(_anchor("t1")) == [str(target.resolve())]


class TestRealHarnessEdit:
    """Restores against the real harness ``file_system.edit``, end to end.

    The hand-built fixtures above anchor whole lines; the real tool matches
    substrings that can start mid-line, computes its blocks' start lines on
    the content after the edit's earlier matches, and carries the pre-edit
    text in its annotations. These run the tool, mirror the adapter's
    annotation-to-occurrence mapping, and assert the recorder restores the
    exact original file.
    """

    @staticmethod
    def _harness_edit_effect(
        target: Path, *, turn: str, edit: dict[str, Any]
    ) -> PublicEffectEntry:
        file_tools = pytest.importorskip(
            "mistralai_vibe_local_harness.vibe._file_tools"
        )
        sandbox_helper = pytest.importorskip(
            "mistralai_vibe_local_harness.vibe._sandbox_helper"
        )
        args = file_tools.EditArgs(file_path=str(target), **edit)
        _, previews, previous_content = sandbox_helper.edit(
            target, args.old_string, args.new_string, replace_all=args.replace_all
        )
        occurrences = [
            (
                block["old_start_line"],
                block["new_start_line"],
                "".join(block["old_lines"]),
                "".join(block["new_lines"]),
            )
            for block in previews
        ]
        return _edit_effect(
            target,
            turn=turn,
            occurrences=occurrences,
            previous_content=previous_content,
        )

    def _assert_restored(
        self, tmp_path: Path, original: str, edit: dict[str, Any]
    ) -> None:
        target = tmp_path / "alpha.txt"
        target.write_bytes(original.encode("utf-8"))
        recorder = _recorder(tmp_path)
        effect = self._harness_edit_effect(target, turn="t1", edit=edit)
        post = target.read_bytes().decode("utf-8")
        assert post != original

        _run(recorder, "t1", [effect], mid_turn=_write(target, post))

        recorder.restore(_anchor("t1"))
        assert target.read_bytes().decode("utf-8") == original

    def test_a_mid_line_edit_restores_exactly(self, tmp_path: Path) -> None:
        self._assert_restored(
            tmp_path, "x = foo\ny = 1\n", _edit(old_string="foo", new_string="bar")
        )

    def test_a_replace_all_edit_restores_exactly(self, tmp_path: Path) -> None:
        self._assert_restored(
            tmp_path,
            "foo()\nz = foo + 1\n",
            _edit(old_string="foo", new_string="qux", replace_all=True),
        )

    def test_a_line_count_change_restores_exactly(self, tmp_path: Path) -> None:
        self._assert_restored(
            tmp_path, "a\nb\nc\nd\n", _edit(old_string="a", new_string="a1\na2")
        )

    def test_a_crlf_edit_restores_its_endings(self, tmp_path: Path) -> None:
        self._assert_restored(
            tmp_path,
            "one\r\ntwo\r\nthree\r\n",
            _edit(old_string="two\r\n", new_string="TWO\r\n"),
        )

    def test_a_last_line_without_a_newline_restores_exactly(
        self, tmp_path: Path
    ) -> None:
        self._assert_restored(
            tmp_path, "one\ntwo", _edit(old_string="two", new_string="TWO")
        )

    def test_a_bom_first_line_edit_restores_exactly(self, tmp_path: Path) -> None:
        self._assert_restored(
            tmp_path, "\ufeffone\ntwo\n", _edit(old_string="one\n", new_string="ONE\n")
        )


def _edit(
    *, old_string: str, new_string: str, replace_all: bool = False
) -> dict[str, Any]:
    """The real harness edit's arguments, less the file path."""
    return {
        "old_string": old_string,
        "new_string": new_string,
        "replace_all": replace_all,
    }


class TestOccurrenceFallback:
    """The positional reversal when an edit carries no pre-edit text.

    A harness without the carried text (or an oversized file) leaves the
    recorded pre-state to reverse the effect's hunks. Hunk positions come
    from the effect's own anchors, and a hunk that is not line-aligned is
    skipped: a stale reconstruction is recoverable, a corrupted one is not.
    """

    def test_a_mid_line_hunk_skips_rather_than_corrupts(self, tmp_path: Path) -> None:
        original = "x = foo\ny = 1\n"
        post = "x = bar\ny = 1\n"
        target = tmp_path / "alpha.txt"
        target.write_text(post, encoding="utf-8")
        recorder = _recorder(tmp_path)
        effect = _edit_effect(
            target,
            turn="t1",
            old_string="foo",
            new_string="bar",
            start_line=1,
            new_start_line=1,
        )

        _run(recorder, "t1", [effect])

        # Unaligned hunk without an exact pre-state: skipped, not spliced.
        assert recorder.restorable_paths(_anchor("t1")) == []
        recorder.restore(_anchor("t1"))
        assert _text(target) == post
        assert _text(target) != original

    def test_a_mid_line_deletion_skips_rather_than_corrupts(
        self, tmp_path: Path
    ) -> None:
        post = "x = \ny = 1\n"
        target = tmp_path / "alpha.txt"
        target.write_text(post, encoding="utf-8")
        recorder = _recorder(tmp_path)
        effect = _edit_effect(
            target,
            turn="t1",
            old_string="foo",
            new_string="",
            start_line=1,
            new_start_line=1,
        )

        _run(recorder, "t1", [effect])

        # A deletion leaves no post-edit text at its position to verify the
        # hunk against, so like an unaligned hunk it is skipped: the file
        # keeps its post-edit content rather than taking a corrupted splice.
        assert recorder.restorable_paths(_anchor("t1")) == []
        recorder.restore(_anchor("t1"))
        assert _text(target) == post

    def test_multi_block_hunks_undo_through_their_own_anchors(
        self, tmp_path: Path
    ) -> None:
        original = "a\nb\nc\nd\n"
        post = "a1\na2\nb\nc\nD\n"
        target = tmp_path / "alpha.txt"
        target.write_text(post, encoding="utf-8")
        recorder = _recorder(tmp_path)
        # The second block's anchor is computed on the content after the
        # first block was applied: line 5, already shifted, never re-shifted.
        effect = _edit_effect(
            target, turn="t1", occurrences=[(1, 1, "a", "a1\na2"), (5, 5, "d", "D")]
        )

        _run(recorder, "t1", [effect])

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_a_later_line_count_change_does_not_shift_earlier_anchors(
        self, tmp_path: Path
    ) -> None:
        """A later hunk's line delta must not be added to an earlier anchor.

        Anchors already name the post-hunk line, and splices run last to
        first, so the earlier anchor matches again once the later hunk is
        undone. Shifting it skips that hunk and records a half-undone file.
        """
        original = "a\nb\nc\nd\n"
        post = "a1\na2\nb\nc\nd1\nd2\n"
        target = tmp_path / "alpha.txt"
        target.write_text(post, encoding="utf-8")
        recorder = _recorder(tmp_path)
        effect = _edit_effect(
            target,
            turn="t1",
            occurrences=[(1, 1, "a", "a1\na2"), (5, 5, "d", "d1\nd2")],
        )

        _run(recorder, "t1", [effect])

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_replace_all_line_count_changes_undo_at_each_anchor(
        self, tmp_path: Path
    ) -> None:
        original = "a\nb\na\n"
        post = "a1\na2\nb\na1\na2\n"
        target = tmp_path / "alpha.txt"
        target.write_text(post, encoding="utf-8")
        recorder = _recorder(tmp_path)
        # The second match's anchor is already shifted by the first match.
        effect = _edit_effect(
            target,
            turn="t1",
            occurrences=[(1, 1, "a", "a1\na2"), (3, 4, "a", "a1\na2")],
        )

        _run(recorder, "t1", [effect])

        recorder.restore(_anchor("t1"))
        assert _text(target) == original

    def test_an_out_of_range_anchor_skips_rather_than_splices(
        self, tmp_path: Path
    ) -> None:
        post = "a\nb\nc\nd\n"
        target = tmp_path / "alpha.txt"
        target.write_text(post, encoding="utf-8")
        recorder = _recorder(tmp_path)
        # A malformed hunk naming a line before the file starts: a negative
        # position would slice from the end and splice "c" there.
        effect = _edit_effect(target, turn="t1", occurrences=[(-1, None, "X", "c")])

        _run(recorder, "t1", [effect])

        recorder.restore(_anchor("t1"))
        assert _text(target) == post

    def test_crlf_endings_survive_the_reversal(self, tmp_path: Path) -> None:
        original = "one\r\ntwo\r\nthree\r\n"
        post = "one\r\nTWO\r\nthree\r\n"
        target = tmp_path / "alpha.txt"
        target.write_bytes(post.encode("utf-8"))
        recorder = _recorder(tmp_path)
        effect = _edit_effect(
            target,
            turn="t1",
            old_string="two\r\n",
            new_string="TWO\r\n",
            start_line=2,
            new_start_line=2,
        )

        _run(recorder, "t1", [effect])

        recorder.restore(_anchor("t1"))
        assert target.read_bytes().decode("utf-8") == original

    def test_a_bom_first_line_hunk_undoes_and_keeps_the_bom(
        self, tmp_path: Path
    ) -> None:
        original = "\ufeffone\ntwo\n"
        post = "\ufeffONE\ntwo\n"
        target = tmp_path / "alpha.txt"
        target.write_bytes(post.encode("utf-8"))
        recorder = _recorder(tmp_path)
        # The harness reads a BOM file as utf-8-sig, so its hunk text never
        # carries the BOM character the disk read keeps on the first line.
        effect = _edit_effect(
            target,
            turn="t1",
            old_string="one\n",
            new_string="ONE\n",
            start_line=1,
            new_start_line=1,
        )

        _run(recorder, "t1", [effect])

        recorder.restore(_anchor("t1"))
        assert target.read_bytes().decode("utf-8") == original

    def test_a_bom_base_restart_stays_aligned_for_an_earlier_first_line_hunk(
        self, tmp_path: Path
    ) -> None:
        original = "\ufeffone\ntwo\n"
        edited = "\ufeffONE\ntwo\n"
        target = tmp_path / "alpha.txt"
        target.write_bytes(edited.encode("utf-8"))
        recorder = _recorder(tmp_path)
        edit = _edit_effect(
            target,
            turn="t1",
            old_string="one\n",
            new_string="ONE\n",
            start_line=1,
            new_start_line=1,
        )
        write = _write_effect(target, turn="t1", content="rewritten", replaced=edited)
        write = write.model_copy(update={"id": f"write-t1-{target.name}"})

        recorder.observe(_state("t1"))
        _write(target, "rewritten")()
        recorder.observe(_state("t1", [edit]))
        recorder.observe(_state("t1", [edit, write]))
        recorder.observe(_state(None))

        recorder.restore(_anchor("t1"))
        assert target.read_bytes().decode("utf-8") == original

    def test_an_edit_with_carried_text_records_it_as_the_exact_base(
        self, tmp_path: Path
    ) -> None:
        original = "one\ntwo\nthree\n"
        post = "one\nTWO\nthree\n"
        target = tmp_path / "alpha.txt"
        target.write_text(post, encoding="utf-8")
        recorder = _recorder(tmp_path)
        effect = _edit_effect(
            target,
            turn="t1",
            old_string="two\n",
            new_string="TWO\n",
            start_line=2,
            new_start_line=2,
            previous_content=original,
        )

        _run(recorder, "t1", [effect])

        assert recorder.restorable_paths(_anchor("t1")) == [str(target)]
        recorder.restore(_anchor("t1"))
        assert _text(target) == original


def test_review_records_edits_first_observed_in_the_terminal_snapshot(
    tmp_path: Path,
) -> None:
    from vibe.core.review import AllTarget

    path = tmp_path / "review.txt"
    original = "one\ntwo\nthree\nfour\nfive\nsix\n"
    path.write_text(original)
    recorder = _recorder(tmp_path)
    recorder.observe(_state("t1"))
    path.write_text("one\nTWO\nthree\nfour\nFIVE\nsix\n")
    first = _edit_effect(
        path, turn="t1", old_string="two", new_string="TWO", start_line=2
    )
    second = _edit_effect(
        path, turn="t1", old_string="five", new_string="FIVE", start_line=5
    ).model_copy(update={"id": "second"})
    recorder.observe(_state(None, [_user_entry("t1"), first, second]))
    review = recorder.review_manager()
    state = review.review_state()
    assert len(state.files) == 1
    assert len(state.files[0].regions) == 2
    assert review.baseline_text(str(path)) == original
    review.revert_review(AllTarget())
    assert path.read_text() == original


def test_review_keeps_manual_edits_and_later_turn_dependencies(tmp_path: Path) -> None:
    from vibe.core.review import AllTarget

    path = tmp_path / "review.txt"
    original = "one\ntwo\nthree\n"
    path.write_text(original)
    recorder = _recorder(tmp_path)
    first = _edit_effect(
        path, turn="t1", old_string="two", new_string="TWO", start_line=2
    )
    _run(recorder, "t1", [first], mid_turn=_write(path, "one\nTWO\nthree\n"))
    path.write_text("one\nTWO\nTHREE\n")
    second = _edit_effect(
        path, turn="t2", old_string="TWO", new_string="EDITED", start_line=2
    )
    _run(recorder, "t2", [second], mid_turn=_write(path, "one\nEDITED\nTHREE\n"))
    review = recorder.review_manager()
    assert len(review.review_state().scopes) == 3
    review.revert_review(AllTarget())
    assert path.read_text() == original


def test_review_reads_a_completed_edit_before_the_turn_settles(tmp_path: Path) -> None:
    from vibe.core.review import AllTarget

    path = tmp_path / "live.txt"
    path.write_text("before\n")
    recorder = _recorder(tmp_path)
    recorder.observe(_state("t1"))
    path.write_text("after\n")
    effect = _edit_effect(
        path, turn="t1", old_string="before", new_string="after", start_line=1
    )
    recorder.observe(_state("t1", [effect]))
    review = recorder.review_manager()
    assert review.baseline_text(str(path)) == "before\n"
    assert len(review.review_state().files) == 1
    # Repeated live reads must not seal or mutate the recorder's open turn.
    assert len(recorder.review_manager().review_state().files) == 1
    recorder.observe(_state(None, [_user_entry("t1"), effect]))
    settled = recorder.review_manager()
    settled.revert_review(AllTarget())
    assert path.read_text() == "before\n"


def test_review_reconstructs_a_turn_after_disk_has_overtaken_effect_delivery(
    tmp_path: Path,
) -> None:
    from vibe.core.review import AllTarget

    path = tmp_path / "coalesced.txt"
    original = "one\ntwo\nthree\nfour\nfive\nsix\n"
    path.write_text(original)
    recorder = _recorder(tmp_path)
    recorder.observe(_state("t1"))
    path.write_text("one\nTWO\nthree\nfour\nFIVE\nsix\n")
    first = _edit_effect(
        path, turn="t1", old_string="two", new_string="TWO", start_line=2
    )
    second = _edit_effect(
        path, turn="t1", old_string="five", new_string="FIVE", start_line=5
    ).model_copy(update={"id": "second"})
    # Disk already includes the second edit when only the first result arrives.
    recorder.observe(_state("t1", [first]))
    recorder.observe(_state("t1", [first, second]))
    recorder.observe(_state(None, [_user_entry("t1"), first, second]))
    review = recorder.review_manager()
    assert len(review.review_state().files[0].regions) == 2
    review.revert_review(AllTarget())
    assert path.read_text() == original


class TestTargetRuntimeResults:
    """Restores from the target runtime's file tool results.

    The target runtime carries a file's replaced text in its own annotation,
    ``mistralai.vibe.harness.file_previous_content``. Each turn here is first
    observed mid-flight, after the tool already wrote, so that carried text is
    the only exact pre-state the recorder gets.
    """

    _PREVIEW_KEY = "mistralai.vibe.harness.file_diff_preview_blocks"
    _PREVIOUS_KEY = "mistralai.vibe.harness.file_previous_content"
    _TRUNCATED = "edit preview truncated because the diff exceeds preview limits"

    @staticmethod
    def _projected(
        tool_name: str,
        arguments: dict[str, Any],
        structured_content: dict[str, Any],
        annotations: dict[str, Any],
    ) -> PublicEffectEntry:
        raw: dict[str, Any] = {
            "type": "effect",
            "id": f"effect-{tool_name}",
            "sessionId": "session-1",
            "turnId": "t1",
            "createdAt": _NOW,
            "updatedAt": _NOW,
            "generationStatus": "completed",
            "title": tool_name,
            "detail": {
                "kind": "tool",
                "toolName": tool_name,
                "input": arguments,
                "display": {"summary": tool_name, "statusText": tool_name},
            },
            "state": {
                "status": "completed",
                "output": {
                    "structured_content": structured_content,
                    **({"_meta": annotations} if annotations else {}),
                },
                "display": {"success": True, "message": tool_name},
            },
        }
        entry = project_unified_history_entry(validate_history_entry(raw))
        assert isinstance(entry, PublicEffectEntry)
        return entry

    @staticmethod
    def _joined_mid_turn(
        tmp_path: Path, effect: PublicEffectEntry
    ) -> RewindFileSnapshots:
        recorder = RewindFileSnapshots(cwd=lambda: str(tmp_path))
        recorder.observe(_state("t1", [effect]))
        recorder.observe(_state(None))
        return recorder

    def test_an_overwrite_restores_the_replaced_text(self, tmp_path: Path) -> None:
        """*Prepare*: A CRLF file the target runtime already overwrote.
        *Do*: Observe its write result, then rewind the turn.
        *Assert*: The file gets its exact replaced bytes back.
        """
        # Prepare
        target = tmp_path / "config.yaml"
        original = b"name: demo\r\nreplicas: 2\r\n"
        target.write_bytes(b"name: demo\r\nreplicas: 5\r\n")
        effect = self._projected(
            "file_system.write_file",
            {"path": str(target), "content": "name: demo\nreplicas: 5\n"},
            {"path": str(target), "bytes_written": 25, "file_existed": True},
            {self._PREVIOUS_KEY: original.decode("utf-8")},
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert target.read_bytes() == original

    def test_a_deletion_edit_restores_the_replaced_text(self, tmp_path: Path) -> None:
        """*Prepare*: A file the target runtime already edited by deleting a
        line, which the hunk reversal cannot undo.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: The file gets its pre-edit text back.
        """
        # Prepare
        target = tmp_path / "alpha.txt"
        original = "one\ntwo\nthree\n"
        target.write_text("one\nthree\n", encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {"file_path": str(target), "old_string": "two\n", "new_string": ""},
            {"file": str(target), "lines_changed": 1, "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": 2,
                        "new_start_line": 2,
                        "old_lines": ["two\n"],
                        "new_lines": [],
                    }
                ],
                self._PREVIOUS_KEY: original,
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == original

    def test_a_truncated_edit_without_blocks_restores_the_replaced_text(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file the target runtime already edited, whose diff
        preview went past its limits and kept no block.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: The file gets its pre-edit text back.
        """
        # Prepare
        target = tmp_path / "app.py"
        original = "a = foo\nb = foo\nc = foo\n"
        target.write_text("a = bar\nb = bar\nc = bar\n", encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": 3, "warnings": [self._TRUNCATED]},
            {self._PREVIOUS_KEY: original},
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == original

    def test_a_truncated_edit_without_replaced_text_is_not_half_undone(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file over the replaced-text limit that the target
        runtime already edited, whose preview kept two of its three blocks.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: The rewind does not undo the two blocks alone: the file keeps
        the edit rather than turning into a mix of both versions.
        """
        # Prepare
        target = tmp_path / "app.py"
        edited = "a = bar\nb = bar\nc = bar\n"
        target.write_text(edited, encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": 3, "warnings": [self._TRUNCATED]},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": line,
                        "new_start_line": line,
                        "old_lines": [f"{name} = foo\n"],
                        "new_lines": [f"{name} = bar\n"],
                    }
                    for line, name in ((1, "a"), (2, "b"))
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == edited

    def test_a_truncated_edit_without_annotations_is_a_file_edit_left_as_is(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file over the replaced-text limit that the target
        runtime already edited, whose preview kept no block: its result has no
        annotations at all.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: The edit shows as a file edit, and the rewind leaves the file
        as edited rather than guessing.
        """
        # Prepare
        target = tmp_path / "big.txt"
        edited = "F\n" * 3
        target.write_text(edited, encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": "filler\n",
                "new_string": "F\n",
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": 3, "warnings": [self._TRUNCATED]},
            {},
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert isinstance(effect.detail, FileEditEffectDetail)
        assert _text(target) == edited

    def test_insertions_without_replaced_text_undo_by_their_blocks(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file the target runtime already edited by inserting
        lines at two places, with blocks as the runtime builds them and no
        replaced text.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: Undoing the blocks gives the pre-edit file back.
        """
        # Prepare
        target = tmp_path / "f.txt"
        original = "x\na\nc\na\nd\n"
        target.write_text("x\na\nb\nb2\nc\na\nb\nb2\nd\n", encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": "a\n",
                "new_string": "a\nb\nb2\n",
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": 4, "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": old,
                        "new_start_line": new,
                        "old_lines": [],
                        "new_lines": ["b\n", "b2\n"],
                    }
                    for old, new in ((2, 3), (4, 7))
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == original

    def test_removals_without_replaced_text_undo_by_their_blocks(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file the target runtime already edited by removing a
        line at two places, before a line the call kept, with blocks as the
        runtime builds them and no replaced text.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: Undoing the blocks, with the line the call kept as context,
        gives the pre-edit file back.
        """
        # Prepare
        target = tmp_path / "f.txt"
        original = "x\nk\ny\nx\nk\nz\n"
        target.write_text("k\ny\nk\nz\n", encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": "x\nk\n",
                "new_string": "k\n",
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": 2, "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": old,
                        "new_start_line": new,
                        "old_lines": ["x\n"],
                        "new_lines": [],
                    }
                    for old, new in ((1, 1), (4, 2))
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == original

    @pytest.mark.parametrize(
        ("original", "old_string", "new_string", "blocks", "restored"),
        [
            pytest.param(
                "x = 1\n    y = 2\n\ndef g():\n    x = 1\n    y = 2\n",
                "x = 1\n    y = 2\n",
                "x = 1\n    z = 3\n    y = 2\n",
                [(1, 2, [], ["    z = 3\n"]), (5, 7, [], ["    z = 3\n"])],
                True,
                id="insertion-after-a-first-line-matched-mid-line",
            ),
            pytest.param(
                "foo\nbar\nfoo()\n",
                "foo",
                "N\nfoo",
                [(1, 1, [], ["N\n"]), (2, 4, [], ["N\n"])],
                True,
                id="insertion-before-a-last-line-matched-without-its-end",
            ),
            pytest.param(
                "x = 1\nD\ny\n    x = 1\nD\ny\n",
                "x = 1\nD\ny\n",
                "x = 1\ny\n",
                [(2, 1, ["D\n"], []), (5, 3, ["D\n"], [])],
                False,
                id="removal-after-a-first-line-matched-mid-line",
            ),
            pytest.param(
                "a\nfoo\nz\na\nfoobar\n",
                "a\nfoo",
                "foo",
                [(1, 1, ["a\n"], []), (4, 2, ["a\n"], [])],
                False,
                id="removal-before-a-last-line-matched-without-its-end",
            ),
        ],
    )
    def test_a_replace_all_matching_part_of_a_line_is_not_half_undone(
        self,
        tmp_path: Path,
        original: str,
        old_string: str,
        new_string: str,
        blocks: list[tuple[int, int, list[str], list[str]]],
        restored: bool,
    ) -> None:
        """*Prepare*: A file over the replaced-text limit that the target
        runtime already edited with a replace_all whose matched text starts or
        ends mid-line at one site and on a line boundary at the other. The
        blocks are the runtime's own, and no replaced text is carried.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: The call's partial line is not taken as the blocks' context
        line, so the rewind treats every site alike: insertions are all undone,
        and removals, which have nothing to check their place against, are all
        left as they are, never one undone and not the other.
        """
        # Prepare
        target = tmp_path / "f.txt"
        edited = original.replace(old_string, new_string)
        target.write_text(edited, encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": len(blocks), "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": old,
                        "new_start_line": new,
                        "old_lines": old_lines,
                        "new_lines": new_lines,
                    }
                    for old, new, old_lines, new_lines in blocks
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == (original if restored else edited)

    @pytest.mark.parametrize(
        ("original", "old_string", "new_string", "blocks"),
        [
            pytest.param(
                "class A:\n    pass\n\nclass B:\n    pass\nclass C:\n    pass\n",
                "    pass\n",
                "    pass\n\n",
                [(3, 4, "\n"), (5, 7, "\n"), (7, 10, "\n")],
                id="blank-line-after-each-body",
            ),
            pytest.param(
                "fn a() {\n}\n\nfn b() {\n}\nfn c() {\n}\n\nend\n",
                "}\n",
                "}\n\n",
                [(3, 4, "\n"), (5, 7, "\n"), (8, 11, "\n")],
                id="blank-line-after-each-brace",
            ),
            pytest.param(
                "import os\nimport sys\nx = 1\nimport os\ny = 2\n",
                "import os\n",
                "import os\nimport sys\n",
                [(2, 3, "import sys\n"), (4, 6, "import sys\n")],
                id="line-equal-to-the-next-one",
            ),
        ],
    )
    def test_insertions_the_runtime_moved_past_equal_lines_rewind_exactly(
        self,
        tmp_path: Path,
        original: str,
        old_string: str,
        new_string: str,
        blocks: list[tuple[int, int, str]],
    ) -> None:
        """*Prepare*: A file over the replaced-text limit that the target
        runtime already edited with a replace_all inserting a line after each
        match. One match is already followed by an equal line, so the runtime
        puts that insertion one line lower than the call tells. The blocks are
        the runtime's own, and no replaced text is carried.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: Every insertion is undone where it is: the file gets its
        pre-edit text back, not a mix of both versions.
        """
        # Prepare
        target = tmp_path / "f.txt"
        edited = original.replace(old_string, new_string)
        target.write_text(edited, encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": len(blocks), "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": old,
                        "new_start_line": new,
                        "old_lines": [],
                        "new_lines": [line],
                    }
                    for old, new, line in blocks
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == original

    def test_removals_the_runtime_moved_past_equal_lines_are_not_half_undone(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file over the replaced-text limit that the target
        runtime already edited with a replace_all removing the line after each
        match. At one match that line equals the next one, so the runtime
        removes the next one instead. The blocks are the runtime's own, and no
        replaced text is carried.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: The rewind does not undo one removal and not the other: the
        file stays as edited.
        """
        # Prepare
        target = tmp_path / "f.txt"
        edited = "a\nb\nx\na\ny\n"
        target.write_text(edited, encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": "a\nb\n",
                "new_string": "a\n",
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": 2, "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": old,
                        "new_start_line": new,
                        "old_lines": ["b\n"],
                        "new_lines": [],
                    }
                    for old, new in ((3, 2), (6, 4))
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == edited

    def test_removals_the_runtime_merged_with_a_kept_line_are_not_half_undone(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file over the replaced-text limit that the target
        runtime already edited with a replace_all removing a whole line. Two
        matches one line apart come as one block that keeps a line, the other
        as a block that only removes. The blocks are the runtime's own, and no
        replaced text is carried.
        *Do*: Project and observe its edit result, then rewind the turn.
        *Assert*: The edit shows the call's strings, and the rewind leaves the
        file as edited rather than bringing back some of the lines.
        """
        # Prepare
        target = tmp_path / "f.py"
        line = "    breakpoint()\n"
        edited = "def f():\n    return 1\n\ndef g():\n    x = 2\n    return x\n"
        target.write_text(edited, encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": line,
                "new_string": "",
                "replace_all": True,
            },
            {"file": str(target), "lines_changed": 3, "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": 2,
                        "new_start_line": 1,
                        "old_lines": [line],
                        "new_lines": [],
                    },
                    {
                        "old_start_line": 6,
                        "new_start_line": 5,
                        "old_lines": [line, "    x = 2\n", line],
                        "new_lines": ["    x = 2\n"],
                    },
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert isinstance(effect.state, CompletedEffectState)
        output = FileEditEffectOutput.model_validate(effect.state.output)
        assert (output.old_string, output.new_string, output.occurrences) == (
            line,
            "",
            [],
        )
        assert _text(target) == edited

    def test_a_removal_before_a_kept_line_undoes_at_that_line(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A file the target runtime already edited by removing a
        line whose call kept the line after it, equal to the line before it,
        with no replaced text.
        *Do*: Observe its edit result, then rewind the turn.
        *Assert*: The undo anchors on the kept line, not on the equal line
        before the removal, and gives the pre-edit file back.
        """
        # Prepare
        target = tmp_path / "f.py"
        original = "x = 1\n\n# removed note\n\ny = 2\n"
        target.write_text("x = 1\n\n\ny = 2\n", encoding="utf-8")
        effect = self._projected(
            "file_system.edit",
            {
                "file_path": str(target),
                "old_string": "# removed note\n\n",
                "new_string": "\n",
            },
            {"file": str(target), "lines_changed": 2, "warnings": []},
            {
                self._PREVIEW_KEY: [
                    {
                        "old_start_line": 3,
                        "new_start_line": 2,
                        "old_lines": ["# removed note\n"],
                        "new_lines": [],
                    }
                ]
            },
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)
        recorder.restore(_anchor("t1"))

        # Assert
        assert _text(target) == original

    def test_an_overwrite_without_replaced_text_restores_nothing(
        self, tmp_path: Path
    ) -> None:
        """*Prepare*: A binary file the target runtime overwrote: its result
        carries no replaced text.
        *Do*: Observe its write result, then rewind the turn.
        *Assert*: Nothing is restorable and the file stays as written.
        """
        # Prepare
        target = tmp_path / "blob.bin"
        target.write_text("hello", encoding="utf-8")
        effect = self._projected(
            "file_system.write_file",
            {"path": str(target), "content": "hello"},
            {"path": str(target), "bytes_written": 5, "file_existed": True},
            {},
        )

        # Do
        recorder = self._joined_mid_turn(tmp_path, effect)

        # Assert
        assert recorder.restorable_paths(_anchor("t1")) == []
        assert recorder.restore(_anchor("t1")) == ([], [])
        assert _text(target) == "hello"
