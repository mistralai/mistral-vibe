from __future__ import annotations

from textual.widgets import Static

from vibe.core.agents import AgentProfile, AgentSafety, BuiltinAgentName

AGENT_ICONS: dict[str, str] = {
    BuiltinAgentName.DEFAULT: "⏵",
    BuiltinAgentName.PLAN: "⏸",
    BuiltinAgentName.ACCEPT_EDITS: "⏵⏵",
    BuiltinAgentName.AUTO_APPROVE: "⏵⏵⏵",
}

SAFETY_CLASSES: dict[AgentSafety, str] = {
    AgentSafety.SAFE: "agent-safe",
    AgentSafety.NEUTRAL: "agent-neutral",
    AgentSafety.DESTRUCTIVE: "agent-destructive",
    AgentSafety.YOLO: "agent-yolo",
}


class AgentIndicator(Static):
    def __init__(self, profile: AgentProfile) -> None:
        super().__init__(markup=False)
        self.can_focus = False
        self.profile = profile
        self._update_display()

    def _update_display(self) -> None:
        icon = AGENT_ICONS.get(self.profile.name, "")
        name = self.profile.display_name.lower()
        self.update(f"{icon}{' ' if icon else ''}{name} agent (shift+tab to cycle)")

        for safety_class in SAFETY_CLASSES.values():
            self.remove_class(safety_class)

        self.add_class(SAFETY_CLASSES[self.profile.safety])

    def set_profile(self, profile: AgentProfile) -> None:
        self.profile = profile
        self._update_display()
