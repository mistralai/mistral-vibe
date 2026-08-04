from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from vibe.core.checkpoints import Checkpointer, FileStore
from vibe.core.types import LLMMessage, MessageList, Role


class SaveMessages(Protocol):
    async def __call__(self, *, allow_empty: bool = False) -> None: ...


class RewindError(Exception):
    """Raised when a rewind operation fails."""


class RewindManager:
    """Impure shell over the shared Checkpointer: restores files from disk and
    truncates and forks the conversation to rewind to an earlier user message.
    """

    def __init__(
        self,
        checkpointer: Checkpointer,
        messages: MessageList,
        save_messages: SaveMessages,
        reset_session: Callable[[], Awaitable[None]],
        files: FileStore | None = None,
    ) -> None:
        self._checkpointer = checkpointer
        self._messages = messages
        self._save_messages = save_messages
        self._reset_session = reset_session
        self._files = files or FileStore()
        self._is_rewinding = False
        self._messages.on_reset(self._on_messages_reset)

    def restorable_paths_at(self, message_index: int) -> list[str]:
        """Paths whose on-disk content would change if rewinding to this turn."""
        history = self._checkpointer.view()
        if not history.has_turn(message_index):
            return []
        plan = history.restore_plan_to_turn(message_index)
        return [
            path for path, target in plan.items() if self._files.read(path) != target
        ]

    def has_file_changes_at(self, message_index: int) -> bool:
        return bool(self.restorable_paths_at(message_index))

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
            restore_errors, restored_paths = self._files.apply(
                self._checkpointer.view().restore_plan_to_turn(message_index)
            )

        if inplace:
            self._truncate(messages, message_index)
            await self._save_messages(allow_empty=True)
        else:
            await self._save_messages()
            self._truncate(messages, message_index)
            await self._reset_session()

        return message_content, restore_errors, restored_paths

    def _truncate(self, messages: Sequence[LLMMessage], message_index: int) -> None:
        self._checkpointer.drop_turns_from(message_index)
        self._is_rewinding = True
        try:
            self._messages.reset(list(messages[:message_index]))
        finally:
            self._is_rewinding = False

    def _on_messages_reset(self) -> None:
        """Called when the message list is reset (session switch, clear, compact, etc.).

        Skipped while rewinding: the rewind's own _truncate handles the log. In
        every other case the checkpoint log is cleared, including mid-act
        compaction: the open turn's turn_id is now stale (it references a
        pre-compaction message index), and keeping it would block
        accepted_turn_frontier with PENDING hunks from a dead context. When a
        turn was open (mid-act compaction), re-open one so the remaining tool
        loop can keep recording snapshots — act() won't call create_checkpoint
        again.
        """
        if self._is_rewinding:
            return
        was_open = self._checkpointer.has_open_turn
        self._checkpointer.clear()
        if was_open:
            self._checkpointer.begin_turn(len(self._messages))
