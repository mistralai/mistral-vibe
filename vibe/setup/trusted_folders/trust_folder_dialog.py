from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import CenterMiddle, Horizontal
from textual.message import Message
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.paths.global_paths import TRUSTED_FOLDERS_FILE


class TrustDialogQuitException(Exception):
    pass


class TrustFolderDialog(CenterMiddle):
    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "move_left", "Left", show=False),
        Binding("right", "move_right", "Right", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("1", "select_1", "Yes", show=False),
        Binding("y", "select_1", "Yes", show=False),
        Binding("2", "select_2", "No", show=False),
        Binding("n", "select_2", "No", show=False),
    ]

    class Trusted(Message):
        pass

    class Untrusted(Message):
        pass

    def __init__(self, folder_path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.folder_path = folder_path
        self.selected_option = 0
        self.option_widgets: list[Static] = []

    def compose(self) -> ComposeResult:
        with CenterMiddle(id="trust-dialog"):
            yield NoMarkupStatic("⚠ Trust this folder?", id="trust-dialog-title")
            yield NoMarkupStatic(
                str(self.folder_path),
                id="trust-dialog-path",
                classes="trust-dialog-path",
            )
            yield NoMarkupStatic(
                "Files that can modify your Mistral Vibe setup were found here. Do you trust this folder?",
                id="trust-dialog-message",
                classes="trust-dialog-message",
            )

            with Horizontal(id="trust-options-container"):
                options = ["Yes", "No"]
                for idx, text in enumerate(options):
                    widget = NoMarkupStatic(
                        f"  {idx + 1}. {text}", classes="trust-option"
                    )
                    self.option_widgets.append(widget)
                    yield widget

            yield NoMarkupStatic(
                "← → navigate  Enter select", classes="trust-dialog-help"
            )

            yield NoMarkupStatic(
                f"Setting will be saved in: {TRUSTED_FOLDERS_FILE.path}",
                id="trust-dialog-save-info",
                classes="trust-dialog-save-info",
            )

    async def on_mount(self) -> None:
        self.selected_option = 1  # Default to "No"
        self._update_options()
        self.focus()

    def _update_options(self) -> None:
        options = ["Yes", "No"]

        if len(self.option_widgets) != len(options):
            return

        for idx, (text, widget) in enumerate(
            zip(options, self.option_widgets, strict=True)
        ):
            is_selected = idx == self.selected_option

            cursor = "› " if is_selected else "  "
            option_text = f"{cursor}{text}"

            widget.update(option_text)

            widget.remove_class("trust-cursor-selected")
            widget.remove_class("trust-option-selected")

            if is_selected:
                widget.add_class("trust-cursor-selected")
            else:
                widget.add_class("trust-option-selected")

    def action_move_left(self) -> None:
        self.selected_option = (self.selected_option - 1) % 2
        self._update_options()

    def action_move_right(self) -> None:
        self.selected_option = (self.selected_option + 1) % 2
        self._update_options()

    def action_select(self) -> None:
        self._handle_selection(self.selected_option)

    def action_select_1(self) -> None:
        self.selected_option = 0
        self._handle_selection(0)

    def action_select_2(self) -> None:
        self.selected_option = 1
        self._handle_selection(1)

    def _handle_selection(self, option: int) -> None:
        match option:
            case 0:
                self.post_message(self.Trusted())
            case 1:
                self.post_message(self.Untrusted())

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)


class TrustFolderApp(App):
    CSS_PATH = "trust_folder_dialog.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit_without_saving", "Quit", show=False, priority=True),
        Binding("ctrl+c", "quit_without_saving", "Quit", show=False, priority=True),
    ]

    def __init__(self, folder_path: Path, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.folder_path = folder_path
        self._result: bool | None = None
        self._quit_without_saving = False

    def on_mount(self) -> None:
        self.theme = "textual-ansi"

    def compose(self) -> ComposeResult:
        yield TrustFolderDialog(self.folder_path)

    def action_quit_without_saving(self) -> None:
        self._quit_without_saving = True
        self.exit()

    def on_trust_folder_dialog_trusted(self, _: TrustFolderDialog.Trusted) -> None:
        self._result = True
        self.exit()

    def on_trust_folder_dialog_untrusted(self, _: TrustFolderDialog.Untrusted) -> None:
        self._result = False
        self.exit()

    def run_trust_dialog(self) -> bool | None:
        self.run()
        if self._quit_without_saving:
            raise TrustDialogQuitException()
        return self._result


def ask_trust_folder(folder_path: Path) -> bool | None:
    app = TrustFolderApp(folder_path)
    return app.run_trust_dialog()
