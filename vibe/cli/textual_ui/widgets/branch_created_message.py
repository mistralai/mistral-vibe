from __future__ import annotations

from vibe.cli.textual_ui.widgets.status_message import StatusMessage
from vibe.utils.display import branch_display


class BranchCreatedMessage(StatusMessage):
    def __init__(self, *, old_session_id: str, new_session_id: str) -> None:
        super().__init__()
        self.add_class("branch-created-message")
        self._old_session_id = old_session_id
        self._new_session_id = new_session_id

    def get_content(self) -> str:
        return branch_display(
            old_session_id=self._old_session_id, new_session_id=self._new_session_id
        )

    def on_mount(self) -> None:
        super().on_mount()
        self.stop_spinning(success=True)
