from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from tests.stubs.fake_filesystem import FakeFilesystem
from vibe.core.checkpoints import (
    Checkpointer,
    CheckpointRecorder,
    FileSnapshot,
    FileState,
    FileStore,
)
from vibe.core.review import FileTarget, ReviewManager
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
    checkpointer: Checkpointer
    recorder: CheckpointRecorder
    review: ReviewManager


def _shells(messages: MessageList) -> _Shells:
    checkpointer = Checkpointer()
    return _Shells(
        checkpointer=checkpointer,
        recorder=CheckpointRecorder(checkpointer, messages),
        review=ReviewManager(checkpointer),
    )


class TestCheckpointRecorder:
    def test_create_checkpoint_carries_forward_snapshots(self, tmp_path: Path) -> None:
        messages = _make_messages("hello", "world")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        f.write_text("v1", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("v2", encoding="utf-8")
        sh.recorder.seal_turn()

        sh.recorder.create_checkpoint()

        # Second checkpoint should have re-read the file
        turns = sh.checkpointer.view().turns
        assert len(turns) == 2
        assert turns[1][1][str(f.resolve())].data == b"v2"

    def test_seal_turn_captures_post_for_touched_files(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        f.write_text("a\n", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("b\n", encoding="utf-8")
        sh.recorder.seal_turn()

        # Sealing captured the post ("b\n") as the turn's edit over its pre
        # ("a\n"): the turn's change is recorded, so reverting the file restores
        # the pre.
        resolved = str(f.resolve())
        assert sh.checkpointer.view().original(resolved) == FileState.from_text("a\n")
        sh.review.revert_review(FileTarget(path=resolved))
        assert f.read_text(encoding="utf-8") == "a\n"

    def test_add_snapshot_no_duplicate(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        sh.recorder.create_checkpoint()

        f = tmp_path / "f.txt"
        f.write_text("content", encoding="utf-8")
        sh.recorder.add_snapshot(_snap(f))
        sh.recorder.add_snapshot(_snap(f))

        resolved = str(f.resolve())
        assert [p for p in sh.checkpointer.view().turns[0][1] if p == resolved] == [
            resolved
        ]

    def test_seal_turn_closes_turn_when_post_read_fails(self) -> None:
        messages = _make_messages("hello")
        checkpointer = Checkpointer()
        fs = FakeFilesystem({"a.txt": b"pre"})
        recorder = CheckpointRecorder(checkpointer, messages, files=FileStore(fs))

        recorder.create_checkpoint()
        recorder.add_snapshot(FileSnapshot(path="a.txt", state=FileState(b"pre")))
        fs.fail_reads.add("a.txt")

        recorder.seal_turn()

        assert not checkpointer.has_open_turn

    def test_seal_turn_read_failure_does_not_mask_other_paths(self) -> None:
        messages = _make_messages("hello")
        checkpointer = Checkpointer()
        fs = FakeFilesystem({"bad.txt": b"pre_bad", "good.txt": b"pre_good"})
        recorder = CheckpointRecorder(checkpointer, messages, files=FileStore(fs))

        recorder.create_checkpoint()
        recorder.add_snapshot(FileSnapshot(path="bad.txt", state=FileState(b"pre_bad")))
        recorder.add_snapshot(
            FileSnapshot(path="good.txt", state=FileState(b"pre_good"))
        )
        fs.files["good.txt"] = b"post_good"
        fs.fail_reads.add("bad.txt")

        recorder.seal_turn()

        assert not checkpointer.has_open_turn
        assert checkpointer.view().content("good.txt").data == b"post_good"
