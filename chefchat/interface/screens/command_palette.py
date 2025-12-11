from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class CommandPalette(ModalScreen):
    """A modal dialog showing available commands."""

    CSS = """
    CommandPalette {
        align: center middle;
    }

    #palette-dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: auto;
        padding: 0 1;
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
    }

    #palette-title {
        column-span: 2;
        height: 3;
        content-align: center middle;
        text-style: bold;
        color: $accent;
        border-bottom: solid $panel-border;
        margin-bottom: 1;
    }

    .cmd-name {
        text-style: bold;
        color: $info;
    }

    .cmd-desc {
        color: $text-muted;
    }

    Button {
        width: 100%;
        margin-top: 1;
        column-span: 2;
    }
    """

    COMMANDS = [
        ("/help", "Show this help menu"),
        ("/clear", "Clear tickets, plate, and reset stations"),
        ("/chef [task]", "Ask the Sous Chef to plan a task"),
        ("/plate", "Show current plate status"),
        ("/quit", "Exit the kitchen"),
    ]

    def compose(self) -> ComposeResult:
        yield Grid(
            Label("👨‍🍳 ChefChat Command Menu", id="palette-title"),
            *[
                item
                for cmd, desc in self.COMMANDS
                for item in (
                    Label(cmd, classes="cmd-name"),
                    Label(desc, classes="cmd-desc"),
                )
            ],
            Button("Close Menu", variant="primary", id="close_btn"),
            id="palette-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close_btn":
            self.dismiss()
