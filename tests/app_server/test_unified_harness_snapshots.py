"""Unit coverage for the unified backend's rewind file snapshots."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from vibe.app_server._effect_models import (
    FileEditEffectDetail,
    FileEditEffectInput,
    FileWriteEffectDetail,
    FileWriteEffectInput,
    FileWriteEffectOutput,
)
from vibe.app_server._unified_harness_snapshots import RewindFileSnapshots
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
            output=output.model_dump(mode="json", by_alias=True),
            display=EffectResultDisplay(success=True, message=str(path)),
        ),
    )


def _edit_effect(path: Path, *, turn: str) -> PublicEffectEntry:
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
                file_path=str(path), old_string="a", new_string="b"
            ),
        ),
        state=CompletedEffectState(
            output=None, display=EffectResultDisplay(success=True, message=str(path))
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
            [_edit_effect(target, turn="t1")],
            mid_turn=_write(target, "edited"),
        )
        assert recorder.restorable_paths(_anchor("t1")) == []

        second = _write_effect(
            target, turn="t2", content="rewritten", replaced="edited"
        )
        _run(recorder, "t2", [second], mid_turn=_write(target, "rewritten"))
        recorder.restore(_anchor("t2"))

        assert _text(target) == "edited"

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
