from __future__ import annotations

from textual.widgets import Static


class CalmStatus(Static):
    def __init__(self) -> None:
        super().__init__("")
        self.add_class("calm-status")
        self.display = False

    def set_enabled(self, enabled: bool) -> None:
        self.display = enabled
        self.update("[$primary]☾[/] calm mode" if enabled else "")
