from __future__ import annotations

from textual.widgets import Static


class ModeIndicator(Static):
    def __init__(self, auto_approve: bool = False) -> None:
        super().__init__()
        self.can_focus = False
        self._auto_approve = auto_approve
        self._update_display()

    def _update_display(self) -> None:
        if self._auto_approve:
            self.update("⏵⏵ auto-approve on (shift+tab to toggle)")
            self.add_class("mode-on")
            self.remove_class("mode-off")
        else:
            self.update("⏵ auto-approve off (shift+tab to toggle)")
            self.add_class("mode-off")
            self.remove_class("mode-on")

    def set_auto_approve(self, enabled: bool) -> None:
        self._auto_approve = enabled
        self._update_display()
