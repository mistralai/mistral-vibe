from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

if TYPE_CHECKING:
    from vibe.core.plugins.models import PluginEntry, PluginScope

_SCOPE_LABELS: dict[str, str] = {"user": "USR", "project": "PRJ", "local": "LCL"}


def _build_option_text(name: str, entry: PluginEntry, scope: PluginScope) -> Text:
    text = Text(no_wrap=True)
    status = "✓" if entry.enabled else "✗"
    style = "green" if entry.enabled else "dim red"
    text.append(f" {status} ", style=style)
    text.append(f"{name:24}", style="bold" if entry.enabled else "dim")
    text.append(f"  {entry.version:10}", style="dim")
    scope_label = _SCOPE_LABELS.get(scope.value, scope.value)
    text.append(f"  {scope_label:4}", style="cyan")
    text.append(f"  {entry.source.value}", style="dim")
    return text


class PluginPickerApp(Container):
    """Plugin picker for /plugin command — list and toggle plugins."""

    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]

    class PluginToggled(Message):
        def __init__(self, plugin_name: str, enabled: bool) -> None:
            self.plugin_name = plugin_name
            self.enabled = enabled
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(
        self, plugins: dict[str, tuple[PluginScope, PluginEntry]], **kwargs: Any
    ) -> None:
        super().__init__(id="pluginpicker-app", **kwargs)
        self._plugins = plugins

    def compose(self) -> ComposeResult:
        options = [
            Option(_build_option_text(name, entry, scope), id=name)
            for name, (scope, entry) in self._plugins.items()
        ]
        with Vertical(id="pluginpicker-content"):
            yield OptionList(*options, id="pluginpicker-options")
            yield NoMarkupStatic(
                "Up/Down Navigate  Enter Toggle  Esc Cancel",
                classes="pluginpicker-help",
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option.id:
            return
        name = event.option.id
        scope, entry = self._plugins[name]
        new_enabled = not entry.enabled

        self.post_message(self.PluginToggled(name, new_enabled))

        display_entry = entry.model_copy(update={"enabled": new_enabled})
        self._plugins[name] = (scope, display_entry)
        option_list = self.query_one(OptionList)
        idx = event.option_index
        option_list.replace_option_prompt(
            name, _build_option_text(name, display_entry, scope)
        )
        option_list.highlighted = idx

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
