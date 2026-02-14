from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, TypedDict

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

if TYPE_CHECKING:
    from vibe.core.agent_loop import AgentLoop


class AgentSettingDefinition(TypedDict):
    key: str
    label: str
    type: str
    options: list[str]
    category: str  # "agent" or "model"


class AgentApp(Container):
    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("space", "toggle_setting", "Toggle", show=False),
        Binding("enter", "cycle", "Next", show=False),
    ]

    class AgentChanged(Message):
        def __init__(self, agent_name: str) -> None:
            super().__init__()
            self.agent_name = agent_name

    class ModelChanged(Message):
        def __init__(self, model_alias: str) -> None:
            super().__init__()
            self.model_alias = model_alias

    class AgentClosed(Message):
        def __init__(
            self, agent_changes: dict[str, str], model_changes: dict[str, str]
        ) -> None:
            super().__init__()
            self.agent_changes = agent_changes
            self.model_changes = model_changes

    def __init__(self, agent_loop: AgentLoop) -> None:
        super().__init__(id="agent-app")
        self.agent_loop = agent_loop
        self.selected_index = 0
        self.agent_changes: dict[str, str] = {}
        self.model_changes: dict[str, str] = {}

        # Build settings from available agents and models
        self.settings: list[AgentSettingDefinition] = []

        # Add agent selection
        available_agents = agent_loop.agent_manager.available_agents
        agent_options = sorted([
            f"{agent.display_name} - {agent.description}"
            for agent in available_agents.values()
        ])
        self.settings.append({
            "key": "agent",
            "label": "Agent",
            "type": "cycle",
            "options": agent_options,
            "category": "agent",
        })

        # Add model selection (reusing config model options)
        model_options = [m.alias for m in agent_loop.config.models]
        self.settings.append({
            "key": "active_model",
            "label": "Model",
            "type": "cycle",
            "options": model_options,
            "category": "model",
        })

        self.title_widget: Static | None = None
        self.setting_widgets: list[Static] = []
        self.help_widget: Static | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-content"):
            self.title_widget = NoMarkupStatic(
                "Agent & Model Settings", classes="settings-title"
            )
            yield self.title_widget

            yield NoMarkupStatic("")

            for _ in self.settings:
                widget = NoMarkupStatic("", classes="settings-option")
                self.setting_widgets.append(widget)
                yield widget

            yield NoMarkupStatic("")

            self.help_widget = NoMarkupStatic(
                "↑↓ navigate  Space/Enter toggle  ESC exit", classes="settings-help"
            )
            yield self.help_widget

    def on_mount(self) -> None:
        self._update_display()
        self.focus()

    def _get_display_value(self, setting: AgentSettingDefinition) -> str:
        key = setting["key"]
        if key in self.agent_changes:
            return self.agent_changes[key]
        elif key in self.model_changes:
            return self.model_changes[key]

        if key == "agent":
            current_agent = self.agent_loop.agent_profile
            return f"{current_agent.display_name} - {current_agent.description}"
        elif key == "active_model":
            return self.agent_loop.config.active_model
        return ""

    def _update_display(self) -> None:
        for i, (setting, widget) in enumerate(
            zip(self.settings, self.setting_widgets, strict=True)
        ):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "

            label: str = setting["label"]
            value: str = self._get_display_value(setting)

            text = f"{cursor}{label}: {value}"

            widget.update(text)

            widget.remove_class("settings-cursor-selected")
            widget.remove_class("settings-value-cycle-selected")
            widget.remove_class("settings-value-cycle-unselected")

            if is_selected:
                widget.add_class("settings-value-cycle-selected")
            else:
                widget.add_class("settings-value-cycle-unselected")

    def action_move_up(self) -> None:
        self.selected_index = (self.selected_index - 1) % len(self.settings)
        self._update_display()

    def action_move_down(self) -> None:
        self.selected_index = (self.selected_index + 1) % len(self.settings)
        self._update_display()

    def action_toggle_setting(self) -> None:
        setting = self.settings[self.selected_index]
        key: str = setting["key"]
        current: str = self._get_display_value(setting)

        options: list[str] = setting["options"]
        new_value = ""
        try:
            current_idx = options.index(current)
            next_idx = (current_idx + 1) % len(options)
            new_value = options[next_idx]
        except (ValueError, IndexError):
            new_value = options[0] if options else current

        if setting["category"] == "agent":
            self.agent_changes[key] = new_value
            self.post_message(self.AgentChanged(agent_name=new_value))
        else:
            self.model_changes[key] = new_value
            self.post_message(self.ModelChanged(model_alias=new_value))

        self._update_display()

    def action_cycle(self) -> None:
        self.action_toggle_setting()

    def action_close(self) -> None:
        self.post_message(
            self.AgentClosed(
                agent_changes=self.agent_changes, model_changes=self.model_changes
            )
        )

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)
