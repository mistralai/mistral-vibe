from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from vibe.core.rewind.manager import FileSnapshot, RewindError, RewindManager
from vibe.core.types import LLMMessage, MessageList, Role


def _make_messages(*contents: str) -> MessageList:
    """Create a MessageList with a system message followed by user/assistant pairs."""
    msgs = MessageList([LLMMessage(role=Role.system, content="system")])
    for content in contents:
        msgs.append(LLMMessage(role=Role.user, content=content))
        msgs.append(LLMMessage(role=Role.assistant, content=f"reply to {content}"))
    return msgs


def _snap(path: Path) -> FileSnapshot:
    """Create a FileSnapshot by reading a file (or None if missing)."""
    resolved = str(path.resolve())
    try:
        content: bytes | None = path.read_bytes()
    except FileNotFoundError:
        content = None
    return FileSnapshot(path=resolved, content=content)


def _make_manager(
    messages: MessageList,
) -> tuple[RewindManager, list[bool], list[bool]]:
    save_calls: list[bool] = []
    reset_calls: list[bool] = []

    async def save_messages() -> None:
        save_calls.append(True)

    def reset_session() -> None:
        reset_calls.append(True)

    mgr = RewindManager(
        messages=messages, save_messages=save_messages, reset_session=reset_session
    )
    return mgr, save_calls, reset_calls


class TestCheckpoints:
    def test_create_checkpoint_carries_forward_snapshots(self, tmp_path: Path) -> None:
        messages = _make_messages("hello", "world")
        mgr, _, _ = _make_manager(messages)
        f = tmp_path / "f.txt"
        f.write_text("v1", encoding="utf-8")

        mgr.create_checkpoint()
        mgr.add_snapshot(_snap(f))
        f.write_text("v2", encoding="utf-8")

        mgr.create_checkpoint()

        # Second checkpoint should have re-read the file
        assert len(mgr.checkpoints) == 2
        assert mgr.checkpoints[1].files[0].content == b"v2"

    def test_add_snapshot_to_all_checkpoints(self, tmp_path: Path) -> None:
        messages = _make_messages("hello", "world")
        mgr, _, _ = _make_manager(messages)

        mgr.create_checkpoint()
        mgr.create_checkpoint()

        f = tmp_path / "late.txt"
        f.write_text("content", encoding="utf-8")
        mgr.add_snapshot(_snap(f))

        resolved = str(f.resolve())
        assert any(s.path == resolved for s in mgr.checkpoints[0].files)
        assert any(s.path == resolved for s in mgr.checkpoints[1].files)

    def test_add_snapshot_no_duplicate(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)
        mgr.create_checkpoint()

        f = tmp_path / "f.txt"
        f.write_text("content", encoding="utf-8")
        mgr.add_snapshot(_snap(f))
        mgr.add_snapshot(_snap(f))

        resolved = str(f.resolve())
        matches = [s for s in mgr.checkpoints[0].files if s.path == resolved]
        assert len(matches) == 1

    def test_has_changes_detects_new_file(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)
        f = tmp_path / "new.txt"

        mgr.create_checkpoint()
        mgr.add_snapshot(FileSnapshot(path=str(f.resolve()), content=None))
        assert not mgr.has_file_changes_at(len(messages))

        f.write_text("created", encoding="utf-8")
        assert mgr.has_file_changes_at(len(messages))

    def test_has_changes_false_when_unchanged(self, tmp_path: Path) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)
        f = tmp_path / "f.txt"
        f.write_text("content", encoding="utf-8")

        mgr.create_checkpoint()
        mgr.add_snapshot(_snap(f))
        assert not mgr.has_file_changes_at(len(messages))

    def test_has_file_changes_at_no_checkpoint(self) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)
        assert not mgr.has_file_changes_at(1)


class TestRewind:
    def test_get_rewindable_messages(self) -> None:
        messages = _make_messages("hello", "world")
        mgr, _, _ = _make_manager(messages)

        result = mgr.get_rewindable_messages()

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
        mgr, _, _ = _make_manager(messages)

        result = mgr.get_rewindable_messages()

        assert len(result) == 2
        assert result[0] == (1, "hello")
        # Index 3 is the injected message — it must be skipped
        assert result[1] == (4, "world")

    @pytest.mark.asyncio
    async def test_rewind_to_message(self) -> None:
        messages = _make_messages("hello", "world")
        mgr, save_calls, reset_calls = _make_manager(messages)

        content, errors = await mgr.rewind_to_message(3, restore_files=False)

        assert content == "world"
        assert errors == []
        assert len(save_calls) == 1
        assert len(reset_calls) == 1
        assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_rewind_to_message_invalid_index(self) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)

        with pytest.raises(RewindError, match="Invalid message index"):
            await mgr.rewind_to_message(99, restore_files=False)

    @pytest.mark.asyncio
    async def test_rewind_to_message_not_user(self) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)

        with pytest.raises(RewindError, match="not a user message"):
            await mgr.rewind_to_message(2, restore_files=False)

    def test_messages_reset_clears_checkpoints(self) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)
        mgr.create_checkpoint()

        assert len(mgr.checkpoints) == 1

        messages.reset([LLMMessage(role=Role.system, content="system")])

        assert len(mgr.checkpoints) == 0

    @pytest.mark.asyncio
    async def test_rewind_preserves_earlier_checkpoints(self) -> None:
        messages = _make_messages("hello", "world")
        mgr, _, _ = _make_manager(messages)

        mgr.create_checkpoint()
        mgr._checkpoints[-1].message_index = 1
        mgr.create_checkpoint()
        mgr._checkpoints[-1].message_index = 3

        assert len(mgr.checkpoints) == 2

        await mgr.rewind_to_message(3, restore_files=False)

        assert len(mgr.checkpoints) == 1

    def test_update_system_prompt_preserves_checkpoints(self) -> None:
        """Switching agents via shift+tab calls update_system_prompt which must
        NOT clear rewind checkpoints (unlike a full reset).
        """
        messages = _make_messages("hello", "world")
        mgr, _, _ = _make_manager(messages)

        mgr.create_checkpoint()
        mgr._checkpoints[-1].message_index = 1
        mgr.create_checkpoint()
        mgr._checkpoints[-1].message_index = 3

        assert len(mgr.checkpoints) == 2

        # Simulate shift+tab agent switch: only the system prompt changes
        messages.update_system_prompt("new agent system prompt")

        assert len(mgr.checkpoints) == 2
        assert messages[0].content == "new agent system prompt"

    def test_create_checkpoint_uses_current_message_count(self) -> None:
        messages = _make_messages("hello")
        mgr, _, _ = _make_manager(messages)

        mgr.create_checkpoint()

        assert mgr.checkpoints[0].message_index == len(messages)


class _Turn:
    """Helper that simulates one conversation turn."""

    def __init__(self, mgr: RewindManager, messages: MessageList) -> None:
        self._mgr = mgr
        self._messages = messages

    def begin(self, user_msg: str) -> None:
        """Start a new turn: create checkpoint then append user message.

        This mirrors agent_loop.act() which calls create_checkpoint()
        *before* the user message is added to the message list.
        """
        self._mgr.create_checkpoint()
        self._messages.append(LLMMessage(role=Role.user, content=user_msg))

    def tool_write(self, path: Path, content: str) -> None:
        """Simulate a tool writing to a file (snapshot → write)."""
        self._mgr.add_snapshot(_snap(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tool_delete(self, path: Path) -> None:
        """Simulate a tool deleting a file (snapshot → unlink)."""
        self._mgr.add_snapshot(_snap(path))
        path.unlink()

    def end(self, assistant_reply: str = "ok") -> None:
        """End the turn: append assistant reply."""
        self._messages.append(LLMMessage(role=Role.assistant, content=assistant_reply))


@pytest.mark.asyncio
class TestRewindScenarios:
    @staticmethod
    def _setup() -> tuple[RewindManager, MessageList, _Turn]:
        messages = MessageList([LLMMessage(role=Role.system, content="system")])
        mgr, _, _ = _make_manager(messages)
        return mgr, messages, _Turn(mgr, messages)

    async def test_edit_file_across_turns_rewind_to_middle(
        self, tmp_path: Path
    ) -> None:
        """A file is edited every turn. Rewinding restores the version from
        *before* the target turn.
        """
        mgr, messages, turn = self._setup()
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
        rewindable = mgr.get_rewindable_messages()
        turn2_idx = rewindable[1][0]
        await mgr.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "v1"

    async def test_file_created_then_rewind_before_creation(
        self, tmp_path: Path
    ) -> None:
        """A file that didn't exist before should be deleted on rewind."""
        mgr, messages, turn = self._setup()
        new_file = tmp_path / "generated.py"

        turn.begin("turn1")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(new_file, "print('hello')")
        turn.end()

        turn1_idx = mgr.get_rewindable_messages()[0][0]
        await mgr.rewind_to_message(turn1_idx, restore_files=True)

        assert not new_file.exists()

    async def test_file_deleted_by_tool_rewind_restores(self, tmp_path: Path) -> None:
        """Rewinding past a deletion restores the file."""
        mgr, _, turn = self._setup()
        f = tmp_path / "config.yaml"
        f.write_text("key: value", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, "key: value")  # touch to start tracking
        turn.end()

        turn.begin("turn2")
        turn.tool_delete(f)
        turn.end()

        assert not f.exists()

        turn2_idx = mgr.get_rewindable_messages()[1][0]
        await mgr.rewind_to_message(turn2_idx, restore_files=True)

        assert f.exists()
        assert f.read_text(encoding="utf-8") == "key: value"

    async def test_mixed_create_and_edit(self, tmp_path: Path) -> None:
        """Multiple files: one pre-existing and edited, one created mid-session."""
        mgr, messages, turn = self._setup()
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

        turn1_idx = mgr.get_rewindable_messages()[0][0]
        await mgr.rewind_to_message(turn1_idx, restore_files=True)

        assert existing.read_text(encoding="utf-8") == "original"
        assert not new_file.exists()

    async def test_user_manual_edit_between_turns(self, tmp_path: Path) -> None:
        """If the user edits a file between turns, the checkpoint at the next
        turn captures the user's version, so rewind restores it.
        """
        mgr, _, turn = self._setup()
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
        turn2_idx = mgr.get_rewindable_messages()[1][0]
        await mgr.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "user edited this"

    async def test_rewind_without_restore(self, tmp_path: Path) -> None:
        """Rewinding with restore_files=False truncates messages but keeps
        files as they are.
        """
        mgr, messages, turn = self._setup()
        f = tmp_path / "data.json"
        f.write_text("{}", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, '{"a": 1}')
        turn.end()

        turn.begin("turn2")
        turn.tool_write(f, '{"a": 1, "b": 2}')
        turn.end()

        turn1_idx = mgr.get_rewindable_messages()[0][0]
        await mgr.rewind_to_message(turn1_idx, restore_files=False)

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
        mgr, _, turn = self._setup()
        f = tmp_path / "code.py"
        f.write_text("v0", encoding="utf-8")

        turn.begin("turn1")
        turn.tool_write(f, "v1")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(f, "v2")
        turn.end()

        # Rewind to turn2
        turn2_idx = mgr.get_rewindable_messages()[1][0]
        await mgr.rewind_to_message(turn2_idx, restore_files=True)
        assert f.read_text(encoding="utf-8") == "v1"

        # New turn after rewind
        turn.begin("turn2-bis")
        turn.tool_write(f, "v2-bis")
        turn.end()

        turn.begin("turn3-bis")
        turn.tool_write(f, "v3-bis")
        turn.end()

        # Rewind to turn2-bis
        turn2bis_idx = mgr.get_rewindable_messages()[1][0]
        await mgr.rewind_to_message(turn2bis_idx, restore_files=True)
        assert f.read_text(encoding="utf-8") == "v1"

    async def test_agent_switch_between_turns_preserves_rewind(
        self, tmp_path: Path
    ) -> None:
        """Pressing shift+tab between two messages switches agents, which calls
        update_system_prompt.  Checkpoints must survive so a subsequent rewind
        restores files correctly.
        """
        mgr, messages, turn = self._setup()
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
        turn2_idx = mgr.get_rewindable_messages()[1][0]
        await mgr.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "v1"

    async def test_binary_file_snapshot_and_restore(self, tmp_path: Path) -> None:
        """Binary files (non-UTF-8) are snapshotted and restored correctly."""
        mgr, _, turn = self._setup()
        f = tmp_path / "image.bin"
        original = bytes(range(256))
        f.write_bytes(original)

        turn.begin("turn1")
        mgr.add_snapshot(_snap(f))
        f.write_bytes(b"\x00" * 256)
        turn.end()

        turn1_idx = mgr.get_rewindable_messages()[0][0]
        await mgr.rewind_to_message(turn1_idx, restore_files=True)

        assert f.read_bytes() == original

    async def test_create_edit_delete_full_lifecycle(self, tmp_path: Path) -> None:
        """File goes through create → edit → delete. Rewind to each point
        restores the correct state.
        """
        mgr, _, turn = self._setup()
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
        turn3_idx = mgr.get_rewindable_messages()[2][0]
        await mgr.rewind_to_message(turn3_idx, restore_files=True)
        assert f.read_text(encoding="utf-8") == "edited"

    async def test_user_creates_file_tool_overwrites(self, tmp_path: Path) -> None:
        """User creates a file manually before a turn. The tool overwrites it.
        Rewind restores the user's version.
        """
        mgr, _, turn = self._setup()
        f = tmp_path / "notes.txt"

        turn.begin("turn1")
        turn.end()

        # User creates the file manually between turns
        f.write_text("user notes", encoding="utf-8")

        turn.begin("turn2")
        turn.tool_write(f, "overwritten by tool")
        turn.end()

        turn2_idx = mgr.get_rewindable_messages()[1][0]
        await mgr.rewind_to_message(turn2_idx, restore_files=True)

        assert f.read_text(encoding="utf-8") == "user notes"

    async def test_nested_directory_files(self, tmp_path: Path) -> None:
        """Files in nested directories are restored including parent dirs."""
        mgr, _, turn = self._setup()
        deep = tmp_path / "src" / "pkg" / "module.py"

        turn.begin("turn1")
        turn.tool_write(deep, "def foo(): pass")
        turn.end()

        turn.begin("turn2")
        turn.tool_write(deep, "def foo(): return 42")
        turn.end()

        turn1_idx = mgr.get_rewindable_messages()[0][0]

        # Delete everything
        deep.unlink()
        (tmp_path / "src" / "pkg").rmdir()
        (tmp_path / "src").rmdir()

        await mgr.rewind_to_message(turn1_idx, restore_files=True)

        # File didn't exist before turn1 → should be deleted
        assert not deep.exists()

    async def test_rewind_restores_errors_collected(self, tmp_path: Path) -> None:
        """When removing a file during rewind fails, errors are returned in the tuple."""
        mgr, _, turn = self._setup()
        created_file = tmp_path / "locked.txt"

        turn.begin("turn1")
        turn.end()

        turn.begin("turn2")
        # Snapshot runs before write → earlier checkpoints record content=None
        turn.tool_write(created_file, "created in turn2")
        turn.end()

        turn1_idx = mgr.get_rewindable_messages()[0][0]
        with patch(
            "vibe.core.rewind.manager.os.remove",
            side_effect=OSError("mocked removal failure"),
        ):
            _, errors = await mgr.rewind_to_message(turn1_idx, restore_files=True)

        assert len(errors) == 1
        assert "Failed to delete file" in errors[0]
        assert "locked.txt" in errors[0]
