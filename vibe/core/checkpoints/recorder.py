from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibe.core.checkpoints.checkpointer import Checkpointer
from vibe.core.checkpoints.file_store import FileStore
from vibe.core.checkpoints.models import FileState
from vibe.core.logger import logger

if TYPE_CHECKING:
    from vibe.core.types import MessageList


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    state: FileState


class CheckpointRecorder:
    """Impure write shell over the shared Checkpointer: drives the per-turn
    snapshot lifecycle, re-reading files from disk at turn boundaries. Owned and
    driven by the agent loop; the read shells observe the same Checkpointer.
    """

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
        try:
            for path in self._checkpointer.view().last_turn_paths():
                # Read each path independently: one unreadable file must not skip
                # the rest, or their edits would seal as unchanged (post defaults
                # to pre) and drop from the log while still living on disk.
                try:
                    post = self._files.read(path)
                except OSError as exc:
                    logger.warning(
                        "Failed to read post-edit state for path=%s while sealing "
                        "turn; its change this turn will not be recorded",
                        path,
                        exc_info=exc,
                    )
                    continue
                self._checkpointer.record_post_edit(path, post)
        finally:
            self._checkpointer.seal_turn()
