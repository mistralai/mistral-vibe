from __future__ import annotations

from textual.widgets import Static

from vibe.core.modes import AgentMode, ModeSafety

MODE_ICONS: dict[AgentMode, str] = {
    AgentMode.DEFAULT: "⏵",
    AgentMode.PLAN: "⏸︎",
    AgentMode.ACCEPT_EDITS: "⏵⏵",
    AgentMode.AUTO_APPROVE: "⏵⏵⏵",
}

SAFETY_CLASSES: dict[ModeSafety, str] = {
    ModeSafety.SAFE: "mode-safe",
    ModeSafety.NEUTRAL: "mode-neutral",
    ModeSafety.DESTRUCTIVE: "mode-destructive",
    ModeSafety.YOLO: "mode-yolo",
}


class ModeIndicator(Static):
    """Displays the current agent mode with safety-colored indicator."""

    def __init__(self, mode: AgentMode = AgentMode.DEFAULT) -> None:
        super().__init__()
        self.can_focus = False
        self._mode = mode
        self._update_display()

    def _update_display(self) -> None:
        icon = MODE_ICONS.get(self._mode, "??")
        name = self._mode.display_name.lower()
        self.update(f"{icon} {name} mode (shift+tab to cycle)")

        for safety_class in SAFETY_CLASSES.values():
            self.remove_class(safety_class)

        self.add_class(SAFETY_CLASSES[self._mode.safety])

    @property
    def mode(self) -> AgentMode:
        return self._mode

    def set_mode(self, mode: AgentMode) -> None:
        self._mode = mode
        self._update_display()
