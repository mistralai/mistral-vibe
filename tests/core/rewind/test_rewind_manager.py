from __future__ import annotations

from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

import pytest

from vibe.core.checkpoints import (
    Checkpointer,
    CheckpointRecorder,
    FileSnapshot,
    FileState,
)
from vibe.core.rewind import RewindError, RewindManager
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
    rewind: RewindManager
    save_calls: list[bool]
    reset_calls: list[bool]


def _shells(messages: MessageList) -> _Shells:
    save_calls: list[bool] = []
    reset_calls: list[bool] = []

    async def save_messages(*, allow_empty: bool = False) -> None:
        save_calls.append(True)

    async def reset_session() -> None:
        reset_calls.append(True)

    checkpointer = Checkpointer()
    return _Shells(
        checkpointer=checkpointer,
        recorder=CheckpointRecorder(checkpointer, messages),
        rewind=RewindManager(
            checkpointer,
            messages=messages,
            save_messages=save_messages,
            reset_session=reset_session,
        ),
        save_calls=save_calls,
        reset_calls=reset_calls,
    )


class TestRewindHasChanges:
    def test_has_changes_detects_new_file(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        f = tmp_path / "new.txt"

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(
            FileSnapshot(path=str(f.resolve()), state=FileState(None))
        )
        assert not sh.rewind.has_file_changes_at(len(messages))

        f.write_text("created", encoding="utf-8")
        assert sh.rewind.has_file_changes_at(len(messages))

    def test_has_changes_false_when_unchanged(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        f = tmp_path / "f.txt"
        f.write_text("content", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        assert not sh.rewind.has_file_changes_at(len(messages))

    def test_has_file_changes_at_no_checkpoint(self) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        assert not sh.rewind.has_file_changes_at(1)


class TestRewind:
    def test_get_rewindable_messages(self) -> None:
        messages = _make_messages("hello", "world")
        sh = _shells(messages)

        result = sh.rewind.get_rewindable_messages()

        assert len(result) == 2
        assert result[0] == (1, "hello")
        assert result[1] == (3, "world")

    def test_get_rewindable_messages_excludes_injected(self) -> None:
        messages = _make_messages("hello")
        # Insert an injected middleware message between turns
        messages.append(
            LLMMessage(role=Role.user, content="plan mode reminder", injected=True)
        )
        messages.append(LLMMessage(role=Role.user, content="world"))
        messages.append(LLMMessage(role=Role.assistant, content="reply to world"))
        sh = _shells(messages)

        result = sh.rewind.get_rewindable_messages()

        assert len(result) == 2
        assert result[0] == (1, "hello")
        # Index 3 is the injected message — it must be skipped
        assert result[1] == (4, "world")

    def test_index_for_message_id_resolves_user_message(self) -> None:
        messages = MessageList([LLMMessage(role=Role.system, content="system")])
        messages.append(LLMMessage(role=Role.user, content="hello", message_id="u1"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))
        messages.append(LLMMessage(role=Role.user, content="world", message_id="u2"))
        sh = _shells(messages)

        assert sh.rewind.index_for_message_id("u1") == 1
        assert sh.rewind.index_for_message_id("u2") == 3

    def test_index_for_message_id_skips_injected(self) -> None:
        messages = MessageList([LLMMessage(role=Role.system, content="system")])
        messages.append(
            LLMMessage(role=Role.user, content="ctx", message_id="inj", injected=True)
        )
        sh = _shells(messages)

        with pytest.raises(RewindError, match="No rewindable user message"):
            sh.rewind.index_for_message_id("inj")

    def test_index_for_message_id_unknown_raises(self) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)

        with pytest.raises(RewindError, match="No rewindable user message"):
            sh.rewind.index_for_message_id("ghost")

    @pytest.mark.asyncio
    async def test_rewind_to_message(self) -> None:
        messages = _make_messages("hello", "world")
        sh = _shells(messages)

        content, errors, restored_paths = await sh.rewind.rewind_to_message(
            3, restore_files=False
        )

        assert content == "world"
        assert errors == []
        assert restored_paths == []
        assert len(sh.save_calls) == 1
        assert len(sh.reset_calls) == 1
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_rewind_to_message_fork_saves_full_then_resets(self) -> None:
        messages = _make_messages("hello", "world")
        saved_lengths: list[int] = []
        reset_calls: list[bool] = []

        async def save_messages(*, allow_empty: bool = False) -> None:
            saved_lengths.append(len(messages))

        async def reset_session() -> None:
            reset_calls.append(True)

        rewind = RewindManager(
            Checkpointer(),
            messages=messages,
            save_messages=save_messages,
            reset_session=reset_session,
        )

        await rewind.rewind_to_message(3, restore_files=False)

        # Fork persists the full history before truncating, then forks.
        assert saved_lengths == [5]
        assert reset_calls == [True]
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_rewind_to_message_inplace_saves_truncated_no_reset(self) -> None:
        messages = _make_messages("hello", "world")
        saved_lengths: list[int] = []
        reset_calls: list[bool] = []

        async def save_messages(*, allow_empty: bool = False) -> None:
            saved_lengths.append(len(messages))

        async def reset_session() -> None:
            reset_calls.append(True)

        rewind = RewindManager(
            Checkpointer(),
            messages=messages,
            save_messages=save_messages,
            reset_session=reset_session,
        )

        content, _, _ = await rewind.rewind_to_message(
            3, restore_files=False, inplace=True
        )

        # In-place persists the truncated history under the same session.
        assert content == "world"
        assert saved_lengths == [3]
        assert reset_calls == []
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_rewind_to_first_message_inplace_opts_into_empty_persist(
        self,
    ) -> None:
        messages = _make_messages("hello", "world")
        allow_empty_calls: list[bool] = []

        async def save_messages(*, allow_empty: bool = False) -> None:
            allow_empty_calls.append(allow_empty)

        async def reset_session() -> None:
            pass

        rewind = RewindManager(
            Checkpointer(),
            messages=messages,
            save_messages=save_messages,
            reset_session=reset_session,
        )

        await rewind.rewind_to_message(1, restore_files=False, inplace=True)

        assert allow_empty_calls == [True]
        assert [m.role for m in messages] == [Role.system]

    @pytest.mark.asyncio
    async def test_rewind_to_message_invalid_index(self) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)

        with pytest.raises(RewindError, match="Invalid message index"):
            await sh.rewind.rewind_to_message(99, restore_files=False)

    @pytest.mark.asyncio
    async def test_rewind_to_message_not_user(self) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)

        with pytest.raises(RewindError, match="not a user message"):
            await sh.rewind.rewind_to_message(2, restore_files=False)

    def test_messages_reset_clears_checkpoints(self) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        sh.recorder.create_checkpoint()
        sh.recorder.seal_turn()

        assert len(sh.checkpointer.view().turns) == 1

        messages.reset([LLMMessage(role=Role.system, content="system")])

        assert len(sh.checkpointer.view().turns) == 0

    def test_messages_reset_during_open_turn_clears_and_reopens(self) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        sh.recorder.create_checkpoint()

        assert sh.checkpointer.has_open_turn

        messages.reset([LLMMessage(role=Role.system, content="system")])

        assert sh.checkpointer.has_open_turn
        assert len(sh.checkpointer.view().turns) == 1

    @pytest.mark.asyncio
    async def test_mid_act_compaction_does_not_break_subsequent_snapshots(
        self, tmp_path: Path
    ) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)
        before = tmp_path / "before.py"
        after = tmp_path / "after.py"
        before.write_text("v0", encoding="utf-8")
        after.write_text("v0", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(before))
        before.write_text("edited_before", encoding="utf-8")

        messages.reset([LLMMessage(role=Role.system, content="system")])

        sh.recorder.add_snapshot(_snap(after))
        after.write_text("edited_after", encoding="utf-8")
        sh.recorder.seal_turn()

        view = sh.checkpointer.view()
        assert view.content(str(after.resolve())).data == b"edited_after"
        assert sh.checkpointer.view().turns

    @pytest.mark.asyncio
    async def test_rewind_after_open_turn_reset_uses_newest_turn_mark(
        self, tmp_path: Path
    ) -> None:
        # A mid-turn transcript reset keeps an open mark with a large turn_id while
        # later turns reuse small ids; rewind must cut at the newest matching mark,
        # not the stale one that merely shares the id.
        messages = _make_messages("old")
        sh = _shells(messages)
        f = tmp_path / "app.py"
        f.write_text("v0", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(f))
        f.write_text("old", encoding="utf-8")
        messages.reset([LLMMessage(role=Role.system, content="system")])
        sh.recorder.seal_turn()

        turn = _Turn(sh, messages)
        turn.begin("current1")
        turn.tool_write(f, "v1")
        turn.end()
        turn.begin("current2")
        turn.tool_write(f, "v2")
        turn.end()

        await sh.rewind.rewind_to_message(3, restore_files=True)

        assert f.read_text(encoding="utf-8") == "v1"
        assert [turn_id for turn_id, _pre in sh.checkpointer.view().turns] == [1, 1]

    @pytest.mark.asyncio
    async def test_rewind_to_message_with_no_turn_mark_does_not_restore_files(
        self, tmp_path: Path
    ) -> None:
        messages = _make_messages("hello", "world")
        sh = _shells(messages)
        f = tmp_path / "app.py"
        f.write_text("content", encoding="utf-8")

        assert not sh.rewind.has_file_changes_at(1)
        content, _errors, restored = await sh.rewind.rewind_to_message(
            1, restore_files=True
        )

        assert content == "hello"
        assert restored == []
        assert f.read_text(encoding="utf-8") == "content"

    @pytest.mark.asyncio
    async def test_rewind_after_open_turn_reset_does_not_block_accepted_frontier(
        self, tmp_path: Path
    ) -> None:
        from vibe.core.checkpoints.models import AgentTurn
        from vibe.core.review import ReviewManager, ScopeTarget

        messages = _make_messages("old")
        sh = _shells(messages)
        stale = tmp_path / "stale.py"
        current = tmp_path / "current.py"
        stale.write_text("v0", encoding="utf-8")
        current.write_text("v0", encoding="utf-8")

        sh.recorder.create_checkpoint()
        sh.recorder.add_snapshot(_snap(stale))
        sh.recorder.add_snapshot(_snap(current))
        stale.write_text("old_stale", encoding="utf-8")
        current.write_text("old_current", encoding="utf-8")
        messages.reset([LLMMessage(role=Role.system, content="system")])
        sh.recorder.seal_turn()

        turn = _Turn(sh, messages)
        turn.begin("current1")
        turn.tool_write(current, "new_current")
        turn.end()

        review = ReviewManager(sh.checkpointer)
        review.approve_review(ScopeTarget(AgentTurn(1)))

        assert sh.checkpointer.view().accepted_turn_frontier() == 2

    @pytest.mark.asyncio
    async def test_rewind_preserves_earlier_checkpoints(self) -> None:
        messages = MessageList([LLMMessage(role=Role.system, content="system")])
        sh = _shells(messages)

        sh.recorder.create_checkpoint()
        messages.append(LLMMessage(role=Role.user, content="hello"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))
        sh.recorder.seal_turn()
        sh.recorder.create_checkpoint()
        messages.append(LLMMessage(role=Role.user, content="world"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))

        assert len(sh.checkpointer.view().turns) == 2

        await sh.rewind.rewind_to_message(3, restore_files=False)

        assert len(sh.checkpointer.view().turns) == 1

    def test_update_system_prompt_preserves_checkpoints(self) -> None:
        """Switching agents via shift+tab calls update_system_prompt which must
        NOT clear rewind checkpoints (unlike a full reset).
        """
        messages = MessageList([LLMMessage(role=Role.system, content="system")])
        sh = _shells(messages)

        sh.recorder.create_checkpoint()
        messages.append(LLMMessage(role=Role.user, content="hello"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))
        sh.recorder.seal_turn()
        sh.recorder.create_checkpoint()
        messages.append(LLMMessage(role=Role.user, content="world"))
        messages.append(LLMMessage(role=Role.assistant, content="reply"))

        assert len(sh.checkpointer.view().turns) == 2

        # Simulate shift+tab agent switch: only the system prompt changes
        messages.update_system_prompt("new agent system prompt")

        assert len(sh.checkpointer.view().turns) == 2
        assert messages[0].content == "new agent system prompt"

    def test_create_checkpoint_uses_current_message_count(self) -> None:
        messages = _make_messages("hello")
        sh = _shells(messages)

        sh.recorder.create_checkpoint()

        assert sh.checkpointer.view().turns[0][0] == len(messages)


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


@pytest.mark.asyncio
class TestRewindScenarios:
    @staticmethod
    def _setup() -> tuple[_Shells, MessageList, _Turn]:
        messages = MessageList([LLMMessage(role=Role.system, content="system")])
        sh = _shells(messages)
        return sh, messages, _Turn(sh, messages)

    async def test_edit_file_across_turns_rewind_to_middle(
        self, tmp_path: Path
    ) -> None:
        """A file is edited every turn. Rewinding restores the version from
        *before* the target turn.
        """
        sh, messages, turn = self._setup()
        f = tmp_path / "app.py"
        f.write_text("v0", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, "v1")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(f, "v2")
        turn.end()

        turn.begin("turn3")
        turn.tool_write(f, "v3")
        turn.end()

        # Rewind to turn2 (message index 3) → file should be v1
        rewindable = sh.rewind.get_rewindable_messages()
        turn2_idx = rewindable[1][0]
        await sh.rewind.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "v1"

    async def test_file_created_then_rewind_before_creation(
        self, tmp_path: Path
    ) -> None:
        """A file that didn't exist before should be deleted on rewind."""
        sh, messages, turn = self._setup()
        new_file = tmp_path / "generated.py"

        turn.begin("turn1")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(new_file, "print('hello')")
        turn.end()

        turn1_idx = sh.rewind.get_rewindable_messages()[0][0]
        await sh.rewind.rewind_to_message(turn1_idx, restore_files=True)

        assert not new_file.exists()

    async def test_file_deleted_by_tool_rewind_restores(self, tmp_path: Path) -> None:
        """Rewinding past a deletion restores the file."""
        sh, _, turn = self._setup()
        f = tmp_path / "config.yaml"
        f.write_text("key: value", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, "key: value")  # touch to start tracking
        turn.end()

        turn.begin("turn2")
        turn.tool_delete(f)
        turn.end()

        assert not f.exists()

        turn2_idx = sh.rewind.get_rewindable_messages()[1][0]
        await sh.rewind.rewind_to_message(turn2_idx, restore_files=True)

        assert f.exists()
        assert f.read_text(encoding="utf-8") == "key: value"

    async def test_mixed_create_and_edit(self, tmp_path: Path) -> None:
        """Multiple files: one pre-existing and edited, one created mid-session."""
        sh, messages, turn = self._setup()
        existing = tmp_path / "main.py"
        existing.write_text("original", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(existing, "modified")
        turn.end()

        turn.begin("turn2")
        new_file = tmp_path / "utils.py"
        turn.tool_write(new_file, "def helper(): ...")
        turn.tool_write(existing, "modified again")
        turn.end()

        turn1_idx = sh.rewind.get_rewindable_messages()[0][0]
        await sh.rewind.rewind_to_message(turn1_idx, restore_files=True)

        assert existing.read_text(encoding="utf-8") == "original"
        assert not new_file.exists()

    async def test_user_manual_edit_between_turns(self, tmp_path: Path) -> None:
        """If the user edits a file between turns, the checkpoint at the next
        turn captures the user's version, so rewind restores it.
        """
        sh, _, turn = self._setup()
        f = tmp_path / "readme.md"
        f.write_text("initial", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, "tool wrote this")
        turn.end()

        # User manually edits the file outside the tool loop
        f.write_text("user edited this", encoding="utf-8")

        turn.begin("turn2")
        turn.tool_write(f, "tool overwrote user")
        turn.end()

        # Rewind to turn2 → should restore the user's manual edit
        turn2_idx = sh.rewind.get_rewindable_messages()[1][0]
        await sh.rewind.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "user edited this"

    async def test_rewind_without_restore(self, tmp_path: Path) -> None:
        """Rewinding with restore_files=False truncates messages but keeps
        files as they are.
        """
        sh, messages, turn = self._setup()
        f = tmp_path / "data.json"
        f.write_text("{}", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, '{"a": 1}')
        turn.end()

        turn.begin("turn2")
        turn.tool_write(f, '{"a": 1, "b": 2}')
        turn.end()

        turn1_idx = sh.rewind.get_rewindable_messages()[0][0]
        await sh.rewind.rewind_to_message(turn1_idx, restore_files=False)

        # File untouched
        assert f.read_text(encoding="utf-8") == '{"a": 1, "b": 2}'
        # But messages were truncated
        assert len(messages) == 1  # only system message

    async def test_rewind_then_new_turns_then_rewind_again(
        self, tmp_path: Path
    ) -> None:
        """After a rewind, new turns create new checkpoints. A second rewind
        should work correctly with the new history.
        """
        sh, _, turn = self._setup()
        f = tmp_path / "code.py"
        f.write_text("v0", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, "v1")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(f, "v2")
        turn.end()

        # Rewind to turn2
        turn2_idx = sh.rewind.get_rewindable_messages()[1][0]
        await sh.rewind.rewind_to_message(turn2_idx, restore_files=True)
        assert f.read_text(encoding="utf-8") == "v1"

        # New turn after rewind
        turn.begin("turn2-bis")
        turn.tool_write(f, "v2-bis")
        turn.end()

        turn.begin("turn3-bis")
        turn.tool_write(f, "v3-bis")
        turn.end()

        # Rewind to turn2-bis
        turn2bis_idx = sh.rewind.get_rewindable_messages()[1][0]
        await sh.rewind.rewind_to_message(turn2bis_idx, restore_files=True)
        assert f.read_text(encoding="utf-8") == "v1"

    async def test_agent_switch_between_turns_preserves_rewind(
        self, tmp_path: Path
    ) -> None:
        """Pressing shift+tab between two messages switches agents, which calls
        update_system_prompt.  Checkpoints must survive so a subsequent rewind
        restores files correctly.
        """
        sh, messages, turn = self._setup()
        f = tmp_path / "main.py"
        f.write_text("v0", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, "v1")
        turn.end()

        # User presses shift+tab → agent switch → system prompt replaced
        messages.update_system_prompt("switched agent prompt")

        turn.begin("turn2")
        turn.tool_write(f, "v2")
        turn.end()

        # Rewind to turn2 should restore "v1"
        turn2_idx = sh.rewind.get_rewindable_messages()[1][0]
        await sh.rewind.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "v1"

    async def test_binary_file_snapshot_and_restore(self, tmp_path: Path) -> None:
        """Binary files (non-UTF-8) are snapshotted and restored correctly."""
        sh, _, turn = self._setup()
        f = tmp_path / "image.bin"
        original = bytes(range(256))
        f.write_bytes(original)

        turn.begin("turn1")
        sh.recorder.add_snapshot(_snap(f))
        f.write_bytes(b"\x00" * 256)
        turn.end()

        turn1_idx = sh.rewind.get_rewindable_messages()[0][0]
        await sh.rewind.rewind_to_message(turn1_idx, restore_files=True)

        assert f.read_bytes() == original

    async def test_restored_paths_excludes_unchanged_files(
        self, tmp_path: Path
    ) -> None:
        sh, _, turn = self._setup()
        changed = tmp_path / "changed.txt"
        unchanged = tmp_path / "unchanged.txt"
        changed.write_text("before", encoding="utf-8")
        unchanged.write_text("same", encoding="utf-8")

        turn.begin("turn1")
        sh.recorder.add_snapshot(_snap(changed))
        sh.recorder.add_snapshot(_snap(unchanged))
        changed.write_text("after", encoding="utf-8")
        turn.end()

        turn1_idx = sh.rewind.get_rewindable_messages()[0][0]
        _, _, restored_paths = await sh.rewind.rewind_to_message(
            turn1_idx, restore_files=True
        )

        assert restored_paths == [str(changed.resolve())]
        assert changed.read_text(encoding="utf-8") == "before"
        assert unchanged.read_text(encoding="utf-8") == "same"

    async def test_create_edit_delete_full_lifecycle(self, tmp_path: Path) -> None:
        """File goes through create → edit → delete. Rewind to each point
        restores the correct state.
        """
        sh, _, turn = self._setup()
        f = tmp_path / "temp.txt"

        turn.begin("turn1")
        turn.tool_write(f, "created")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(f, "edited")
        turn.end()

        turn.begin("turn3")
        turn.tool_delete(f)
        turn.end()

        assert not f.exists()

        # Rewind to turn3 → file should be "edited" (state before deletion)
        turn3_idx = sh.rewind.get_rewindable_messages()[2][0]
        await sh.rewind.rewind_to_message(turn3_idx, restore_files=True)
        assert f.read_text(encoding="utf-8") == "edited"

    async def test_user_creates_file_tool_overwrites(self, tmp_path: Path) -> None:
        """User creates a file manually before a turn. The tool overwrites it.
        Rewind restores the user's version.
        """
        sh, _, turn = self._setup()
        f = tmp_path / "notes.txt"

        turn.begin("turn1")
        turn.end()

        # User creates the file manually between turns
        f.write_text("user notes", encoding="utf-8")

        turn.begin("turn2")
        turn.tool_write(f, "overwritten by tool")
        turn.end()

        turn2_idx = sh.rewind.get_rewindable_messages()[1][0]
        await sh.rewind.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "user notes"

    async def test_nested_directory_files(self, tmp_path: Path) -> None:
        """Files in nested directories are restored including parent dirs."""
        sh, _, turn = self._setup()
        deep = tmp_path / "src" / "pkg" / "module.py"

        turn.begin("turn1")
        turn.tool_write(deep, "def foo(): pass")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(deep, "def foo(): return 42")
        turn.end()

        turn1_idx = sh.rewind.get_rewindable_messages()[0][0]

        # Delete everything
        deep.unlink()
        (tmp_path / "src" / "pkg").rmdir()
        (tmp_path / "src").rmdir()

        await sh.rewind.rewind_to_message(turn1_idx, restore_files=True)

        # File didn't exist before turn1 → should be deleted
        assert not deep.exists()

    async def test_rewind_restores_errors_collected(self, tmp_path: Path) -> None:
        """When removing a file during rewind fails, errors are returned in the tuple."""
        sh, _, turn = self._setup()
        created_file = tmp_path / "locked.txt"

        turn.begin("turn1")
        turn.end()

        turn.begin("turn2")
        # Snapshot runs before write → earlier checkpoints record content=None
        turn.tool_write(created_file, "created in turn2")
        turn.end()

        turn1_idx = sh.rewind.get_rewindable_messages()[0][0]
        with patch(
            "vibe.core.checkpoints.fs.os.remove",
            side_effect=OSError("mocked removal failure"),
        ):
            _, errors, restored_paths = await sh.rewind.rewind_to_message(
                turn1_idx, restore_files=True
            )

        assert len(errors) == 1
        assert restored_paths == []
        assert "Failed to delete file" in errors[0]
        assert "locked.txt" in errors[0]
