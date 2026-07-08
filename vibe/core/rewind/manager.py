from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Protocol

from vibe.core.logger import logger
from vibe.core.types import LLMMessage, MessageList, Role


class SaveMessages(Protocol):
    async def __call__(self, *, allow_empty: bool = False) -> None: ...


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
    """Manages conversation rewind: file snapshots, message truncation, and
    either in-place truncation or forking to a new session.
    """

    def __init__(
        self,
        messages: MessageList,
        save_messages: SaveMessages,
        reset_session: Callable[[], Awaitable[None]],
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

    def restorable_paths_at(self, message_index: int) -> list[str]:
        """Paths whose on-disk content would change if rewinding to this turn."""
        checkpoint = self._get_checkpoint(message_index)
        if checkpoint is None:
            return []
        return [
            snap.path
            for snap in checkpoint.files
            if self._read_snapshot(snap.path).content != snap.content
        ]

    def has_file_changes_at(self, message_index: int) -> bool:
        return bool(self.restorable_paths_at(message_index))

    # -- Rewind operations -----------------------------------------------------

    def get_rewindable_messages(self) -> list[tuple[int, str]]:
        """Return (message_index, content) for each user message."""
        return [
            (i, msg.content or "")
            for i, msg in enumerate(self._messages)
            if msg.role == Role.user and msg.content and not msg.injected
        ]

    def index_for_message_id(self, message_id: str) -> int:
        """Resolve a rewindable user message id to its index.

        Raises:
            RewindError: If no non-injected user message carries this id.
        """
        for index, msg in enumerate(self._messages):
            if (
                msg.role == Role.user
                and not msg.injected
                and msg.message_id == message_id
            ):
                return index
        raise RewindError(f"No rewindable user message with id: {message_id}")

    async def rewind_to_message(
        self, message_index: int, *, restore_files: bool, inplace: bool = False
    ) -> tuple[str, list[str], list[str]]:
        """Rewind the session to the given user message index.

        Optionally restores files, then applies one of two persistence
        strategies:

        - ``inplace=False`` (default, fork): save the full history under the
          current session, truncate, then fork to a fresh session so the
          original conversation is preserved as a parent.
        - ``inplace=True``: truncate first, then persist the truncated history
          under the *same* session. The rewound turns are dropped for good and
          no new session is created.

        Returns a tuple of (message_content, restore_errors, restored_paths).

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
        restored_paths: list[str] = []

        if restore_files:
            checkpoint = self._get_checkpoint(message_index)
            if checkpoint:
                restore_errors, restored_paths = self._restore_checkpoint(checkpoint)

        if inplace:
            self._truncate_messages(messages, message_index)
            await self._save_messages(allow_empty=True)
        else:
            await self._save_messages()
            self._truncate_messages(messages, message_index)
            await self._reset_session()

        return message_content, restore_errors, restored_paths

    # -- Private helpers -------------------------------------------------------

    def _truncate_messages(
        self, messages: Sequence[LLMMessage], message_index: int
    ) -> None:
        self._checkpoints = [
            cp for cp in self._checkpoints if cp.message_index < message_index
        ]
        self._is_rewinding = True
        try:
            self._messages.reset(list(messages[:message_index]))
        finally:
            self._is_rewinding = False

    def _get_checkpoint(self, message_index: int) -> Checkpoint | None:
        for cp in self._checkpoints:
            if cp.message_index == message_index:
                return cp
        return None

    def _restore_checkpoint(
        self, checkpoint: Checkpoint
    ) -> tuple[list[str], list[str]]:
        """Restore files on disk to match the checkpoint state."""
        errors: list[str] = []
        restored_paths: list[str] = []
        for snap in checkpoint.files:
            path = Path(snap.path)
            if snap.content is None:
                if not path.exists():
                    continue
                try:
                    os.remove(path)
                    restored_paths.append(snap.path)
                except Exception:
                    errors.append(f"Failed to delete file: {snap.path}")
            else:
                if self._read_snapshot(snap.path).content == snap.content:
                    continue
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(snap.content)
                    restored_paths.append(snap.path)
                except Exception:
                    errors.append(f"Failed to restore file: {snap.path}")
        return errors, restored_paths

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
