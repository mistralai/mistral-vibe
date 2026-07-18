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
from vibe.core.watchdog.snapshots import ConversationSnapshot


def _snapshot_option(index: int, snapshot: ConversationSnapshot) -> Option:
    text = Text(no_wrap=True)
    text.append(f"{index}  ", style="bold")
    text.append(snapshot.display_name[:40])
    text.append(f"  msg:{len(snapshot.messages)}", style="dim")
    return Option(text, id=snapshot.snapshot_id)


class WatchdogSnapshotPickerApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=False)
    ]

    class SnapshotSelected(Message):
        def __init__(self, snapshot_id: str) -> None:
            self.snapshot_id = snapshot_id
            super().__init__()

    class Cancelled(Message):
        pass

    def __init__(self, snapshots: list[ConversationSnapshot]) -> None:
        super().__init__(id="watchdogsnapshotpicker-app")
        self._snapshots = snapshots

    def compose(self) -> ComposeResult:
        options = [
            _snapshot_option(index, snapshot)
            for index, snapshot in enumerate(self._snapshots)
        ]
        options.append(Option("Close picker", id="close"))
        with Vertical(id="watchdog-snapshot-picker-content"):
            yield NoMarkupStatic(
                "Apply Watchdog Snapshot", classes="watchdog-snapshot-title"
            )
            yield NavigableOptionList(*options, id="watchdog-snapshot-options")
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓')} Navigate  {shortcut('Enter')} Apply  "
                    f"{shortcut('Esc')} Close"
                ),
                classes="watchdog-snapshot-help",
            )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == "close" or option_id is None:
            self.post_message(self.Cancelled())
            return
        self.post_message(self.SnapshotSelected(option_id))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())


class WatchdogSnapshotDropApp(Container):
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]

    class Confirmed(Message):
        pass

    class Cancelled(Message):
        pass

    def __init__(self, count: int) -> None:
        super().__init__(id="watchdogsnapshotdrop-app")
        self._count = count

    def compose(self) -> ComposeResult:
        with Vertical(id="watchdog-snapshot-drop-content"):
            yield NoMarkupStatic(
                f"Drop all {self._count} Watchdog snapshots?",
                classes="watchdog-snapshot-title",
            )
            yield NoMarkupStatic(
                "This cannot be undone.", classes="watchdog-snapshot-warning"
            )
            yield NavigableOptionList(
                Option("Cancel", id="cancel"),
                Option("Drop all", id="drop-all"),
                id="watchdog-snapshot-drop-options",
            )
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('↑↓')} Select  {shortcut('Enter')} Confirm  "
                    f"{shortcut('Esc')} Cancel"
                ),
                classes="watchdog-snapshot-help",
            )

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "drop-all":
            self.post_message(self.Confirmed())
            return
        self.post_message(self.Cancelled())

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled())
