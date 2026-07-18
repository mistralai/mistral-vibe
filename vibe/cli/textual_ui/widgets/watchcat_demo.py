from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.watchdog.demo_runner import SCENARIOS, DemoProgress


class WatchcatDemoPickerApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False)
    ]

    class Selected(Message):
        def __init__(self, scenario: str) -> None:
            self.scenario = scenario
            super().__init__()

    class Closed(Message):
        pass

    def __init__(self) -> None:
        super().__init__(id="watchcatdemopicker-app")

    def compose(self) -> ComposeResult:
        options = [
            Option("Run all scenarios sequentially", id="all"),
            Option("Real headless Vibe integration canary", id="headless"),
        ]
        for scenario in SCENARIOS.values():
            text = Text()
            text.append(f"{scenario.name:<22}", style="bold")
            text.append(scenario.title, style="dim")
            options.append(Option(text, id=scenario.name))
        with Vertical(id="watchcat-demo-picker-content"):
            yield NoMarkupStatic("Run Watchcat Demo", classes="watchcat-demo-title")
            yield NavigableOptionList(*options, id="watchcat-demo-options")
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓')} Select  {shortcut('Enter')} Run  "
                    f"{shortcut('Esc')} Close"
                ),
                classes="watchcat-demo-help",
            )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.post_message(self.Selected(event.option.id))

    def action_close(self) -> None:
        self.post_message(self.Closed())


class WatchcatDemoProgressApp(Container):
    can_focus = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Cancel", show=False)
    ]

    class Cancelled(Message):
        pass

    def __init__(self, scenario: str) -> None:
        super().__init__(id="watchcatdemoprogress-app")
        self._scenario = scenario
        self._counts = {
            name: 0 for name in ("protected", "mitigated", "blocked", "degraded")
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="watchcat-demo-progress-content"):
            yield NoMarkupStatic("Watchcat Demo", classes="watchcat-demo-title")
            yield NoMarkupStatic(self._render_waiting(), id="watchcat-demo-progress")
            yield NoMarkupStatic(
                shortcut_hint(f"{shortcut('Esc')} Cancel"), classes="watchcat-demo-help"
            )

    def update_progress(self, progress: DemoProgress) -> None:
        if progress.state == "completed" and progress.classification is not None:
            self._counts[progress.classification] += 1
        status = "RUNNING" if progress.state == "running" else "RECORDED"
        content = self._render_progress(progress, status)
        self.query_one("#watchcat-demo-progress", NoMarkupStatic).update(content)

    def _render_waiting(self) -> str:
        return f"WATCHCAT DEMO [STARTING]\n|\n`-- selection : {self._scenario}"

    def _render_progress(self, progress: DemoProgress, status: str) -> str:
        return "\n".join([
            f"WATCHCAT DEMO [{status}]",
            "|",
            f"+-- scenario  : {progress.current}/{progress.total} {progress.scenario}",
            f"+-- protected : {self._counts['protected']}",
            f"+-- mitigated : {self._counts['mitigated']}",
            f"+-- blocked   : {self._counts['blocked']}",
            f"`-- degraded  : {self._counts['degraded']}",
        ])

    def action_close(self) -> None:
        self.post_message(self.Cancelled())
