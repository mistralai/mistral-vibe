from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path

from vibe.core.logger import logger
from vibe.core.types import LLMMessage, MessageList, Role


class RewindError(Exception):
    """Raised when a rewind operation fails."""


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """Snapshot of a single file's content at a point in time.

    content is None if the file did not exist (was created after the snapshot).
    """

    path: str
    content: bytes | None


@dataclass
class Checkpoint:
    """Snapshot of tracked files taken before a user message."""

    message_index: int
    files: list[FileSnapshot] = field(default_factory=list)


class RewindManager:
    """Manages conversation rewind: file snapshots, message truncation, and session forking."""

    def __init__(
        self,
        messages: MessageList,
        save_messages: Callable[[], Awaitable[None]],
        reset_session: Callable[[], None],
    ) -> None:
        self._checkpoints: list[Checkpoint] = []
        self._messages = messages
        self._save_messages = save_messages
        self._reset_session = reset_session
        self._is_rewinding = False
        self._messages.on_reset(self._on_messages_reset)

    # -- Checkpoint management -------------------------------------------------

    @property
    def checkpoints(self) -> list[Checkpoint]:
        return list(self._checkpoints)

    def create_checkpoint(self) -> None:
        """Snapshot known files and start a new checkpoint at the current message position.

        Files known from the previous checkpoint are re-read from disk so
        that each checkpoint captures the actual state at that point in time.
        """
        files: list[FileSnapshot] = []
        if self._checkpoints:
            for snap in self._checkpoints[-1].files:
                files.append(self._read_snapshot(snap.path))
        self._checkpoints.append(
            Checkpoint(message_index=len(self._messages), files=files)
        )

    def add_snapshot(self, snapshot: FileSnapshot) -> None:
        """Record a file snapshot into every checkpoint that doesn't have it yet."""
        for cp in self._checkpoints:
            if all(s.path != snapshot.path for s in cp.files):
                cp.files.append(snapshot)

    def has_file_changes_at(self, message_index: int) -> bool:
        """Check if files have changed since the checkpoint at *message_index*."""
        checkpoint = self._get_checkpoint(message_index)
        if checkpoint is None:
            return False
        return self._has_changes_since(checkpoint)

    # -- Rewind operations -----------------------------------------------------

    def get_rewindable_messages(self) -> list[tuple[int, str]]:
        """Return (message_index, content) for each user message."""
        return [
            (i, msg.content or "")
            for i, msg in enumerate(self._messages)
            if msg.role == Role.user and msg.content and not msg.injected
        ]

    async def rewind_to_message(
        self, message_index: int, *, restore_files: bool
    ) -> tuple[str, list[str]]:
        """Rewind the session to the given user message index.

        Saves the current session, truncates messages, optionally restores
        files, and forks to a new session.

        Returns a tuple of (message_content, restore_errors).

        Raises:
            RewindError: If the message index is invalid or not a user message.
        """
        messages: Sequence[LLMMessage] = self._messages
        if message_index < 0 or message_index >= len(messages):
            raise RewindError(f"Invalid message index: {message_index}")

        user_msg = messages[message_index]
        if user_msg.role != Role.user:
            raise RewindError(f"Message at index {message_index} is not a user message")

        message_content = user_msg.content or ""
        restore_errors: list[str] = []

        if restore_files:
            checkpoint = self._get_checkpoint(message_index)
            if checkpoint:
                restore_errors = self._restore_checkpoint(checkpoint)

        await self._save_messages()
        self._checkpoints = [
            cp for cp in self._checkpoints if cp.message_index < message_index
        ]
        self._is_rewinding = True
        try:
            self._messages.reset(list(messages[:message_index]))
        finally:
            self._is_rewinding = False
        self._reset_session()

        return message_content, restore_errors

    # -- Private helpers -------------------------------------------------------

    def _get_checkpoint(self, message_index: int) -> Checkpoint | None:
        for cp in self._checkpoints:
            if cp.message_index == message_index:
                return cp
        return None

    def _restore_checkpoint(self, checkpoint: Checkpoint) -> list[str]:
        """Restore files on disk to match the checkpoint state.

        Returns a list of human-readable error messages for files that
        could not be restored (empty when everything succeeded).
        """
        errors: list[str] = []
        for snap in checkpoint.files:
            path = Path(snap.path)
            if snap.content is None:
                if path.exists():
                    try:
                        os.remove(path)
                    except Exception:
                        errors.append(f"Failed to delete file: {snap.path}")
            else:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(snap.content)
                except Exception:
                    errors.append(f"Failed to restore file: {snap.path}")
        return errors

    @staticmethod
    def _has_changes_since(checkpoint: Checkpoint) -> bool:
        for snap in checkpoint.files:
            try:
                current: bytes | None = Path(snap.path).read_bytes()
            except FileNotFoundError:
                current = None
            if current != snap.content:
                return True
        return False

    @staticmethod
    def _read_snapshot(path: str) -> FileSnapshot:
        try:
            content: bytes | None = Path(path).read_bytes()
        except FileNotFoundError:
            content = None
        except Exception:
            logger.warning("Failed to read file for checkpoint: %s", path)
            content = None
        return FileSnapshot(path=path, content=content)

    def _on_messages_reset(self) -> None:
        """Called when the message list is reset (session switch, clear, compact, etc.)."""
        if not self._is_rewinding:
            self._checkpoints.clear()
