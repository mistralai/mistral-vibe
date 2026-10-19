from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibe.core.checkpoints.checkpointer import Checkpointer
from vibe.core.checkpoints.file_store import FileStore
from vibe.core.checkpoints.models import FileState

if TYPE_CHECKING:
    from vibe.core.types import MessageList


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    state: FileState


class FakeCheckpointCapture:
    """Feed file edits into the public Checkpointer API for review tests."""

    def __init__(
        self,
        checkpointer: Checkpointer,
        messages: MessageList,
        files: FileStore | None = None,
    ) -> None:
        self._checkpointer = checkpointer
        self._messages = messages
        self._files = files or FileStore()

    def create_checkpoint(self) -> None:
        """Start a new turn, re-reading known files so those mutated by tools
        that produce no snapshot are still captured.
        """
        carried = self._checkpointer.view().last_turn_paths()
        self._checkpointer.begin_turn(len(self._messages))
        for path in carried:
            self._checkpointer.record_pre_edit(path, self._files.read(path))

    def add_snapshot(self, snapshot: FileSnapshot) -> None:
        self._checkpointer.record_pre_edit(snapshot.path, snapshot.state)

    def seal_turn(self) -> None:
        for path in self._checkpointer.view().last_turn_paths():
            self._checkpointer.record_post_edit(path, self._files.read(path))
        self._checkpointer.seal_turn()
