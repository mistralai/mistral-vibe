from __future__ import annotations

import asyncio
from contextlib import suppress
from enum import StrEnum, auto
from typing import Any, ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import CenterMiddle, Horizontal
from textual.message import Message

from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.update_notifier.update import do_update
from vibe.core.logger import logger


class UpdatePromptResult(StrEnum):
    CONTINUE = auto()
    UPDATED = auto()
    UPDATE_FAILED = auto()
    QUIT = auto()


class UpdateChoice(StrEnum):
    UPDATE = auto()
    CONTINUE = auto()


_CHOICE_LABELS: dict[UpdateChoice, str] = {
    UpdateChoice.UPDATE: "Update now",
    UpdateChoice.CONTINUE: "Continue with current version",
}


class UpdatePromptDialog(CenterMiddle):
    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "move_left", "Left", show=False),
        Binding("right", "move_right", "Right", show=False),
        Binding("enter", "select", "Select", show=False),
    ]

    class Selected(Message):
        def __init__(self, choice: UpdateChoice) -> None:
            super().__init__()
            self.choice = choice

    class UpdateFinished(Message):
        def __init__(self, succeeded: bool) -> None:
            super().__init__()
            self.succeeded = succeeded

    def __init__(
        self, current_version: str, latest_version: str, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.current_version = current_version
        self.latest_version = latest_version
        self.selected: UpdateChoice = UpdateChoice.UPDATE
        self._option_widgets: dict[UpdateChoice, NoMarkupStatic] = {}
        self._is_updating = False

    def compose(self) -> ComposeResult:
        with CenterMiddle(id="update-dialog"):
            yield NoMarkupStatic(
                "A new Vibe release is available", id="update-dialog-title"
            )
            yield NoMarkupStatic(
                f"{self.current_version} → {self.latest_version}",
                id="update-dialog-version",
            )

            with Horizontal(id="update-options-container"):
                for choice in UpdateChoice:
                    widget = NoMarkupStatic(
                        f"  {_CHOICE_LABELS[choice]}", classes="update-option"
                    )
                    self._option_widgets[choice] = widget
                    yield widget

            yield NoMarkupStatic(
                "← → navigate  Enter select", classes="update-dialog-help"
            )

            yield PetitChat(id="update-dialog-spinner")
            yield NoMarkupStatic("Updating mistral-vibe…", id="update-dialog-status")

    async def on_mount(self) -> None:
        spinner = self.query_one("#update-dialog-spinner", PetitChat)
        spinner.display = False
        status = self.query_one("#update-dialog-status", NoMarkupStatic)
        status.display = False
        self._refresh_options()
        self.focus()

    def _refresh_options(self) -> None:
        for choice in UpdateChoice:
            widget = self._option_widgets[choice]
            cursor = "› " if choice == self.selected else "  "
            widget.update(f"{cursor}{_CHOICE_LABELS[choice]}")
            widget.remove_class("update-option--active")
            widget.remove_class("update-option--inactive")
            widget.add_class(
                "update-option--active"
                if choice == self.selected
                else "update-option--inactive"
            )

    def _move(self, delta: int) -> None:
        if self._is_updating:
            return
        choices = list(UpdateChoice)
        idx = (choices.index(self.selected) + delta) % len(choices)
        self.selected = choices[idx]
        self._refresh_options()

    def action_move_left(self) -> None:
        self._move(-1)

    def action_move_right(self) -> None:
        self._move(1)

    def action_select(self) -> None:
        if self._is_updating:
            return
        self.post_message(self.Selected(self.selected))

    async def enter_updating_state(self) -> None:
        self._is_updating = True

        for widget in self._option_widgets.values():
            widget.display = False
        self.query_one("#update-options-container", Horizontal).display = False
        self.query_one(".update-dialog-help", NoMarkupStatic).display = False

        self.query_one("#update-dialog-spinner", PetitChat).display = True
        self.query_one("#update-dialog-status", NoMarkupStatic).display = True

        try:
            succeeded = await do_update()
        except Exception as exc:
            logger.warning("do_update raised unexpectedly", exc_info=exc)
            succeeded = False
        self.post_message(self.UpdateFinished(succeeded=succeeded))

    def on_blur(self, _: events.Blur) -> None:
        if self._is_updating:
            return
        self.call_after_refresh(self.focus)


class UpdatePromptApp(App[UpdatePromptResult]):
    CSS_PATH = "update_prompt_dialog.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+q", "quit_prompt", "Quit", show=False, priority=True),
        Binding("ctrl+c", "quit_prompt", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        current_version: str,
        latest_version: str,
        theme: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.current_version = current_version
        self.latest_version = latest_version
        self._theme_name = theme
        self._dialog: UpdatePromptDialog | None = None
        self._update_task: asyncio.Task[None] | None = None

    def on_mount(self) -> None:
        if self._theme_name is not None:
            self.theme = self._theme_name

    def compose(self) -> ComposeResult:
        self._dialog = UpdatePromptDialog(self.current_version, self.latest_version)
        yield self._dialog

    async def action_quit_prompt(self) -> None:
        if self._update_task is not None and not self._update_task.done():
            self._update_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._update_task
        self.exit(UpdatePromptResult.QUIT)

    def on_update_prompt_dialog_selected(
        self, message: UpdatePromptDialog.Selected
    ) -> None:
        match message.choice:
            case UpdateChoice.UPDATE:
                if self._dialog is None or self._update_task is not None:
                    return
                self._update_task = asyncio.create_task(
                    self._dialog.enter_updating_state()
                )
            case UpdateChoice.CONTINUE:
                self.exit(UpdatePromptResult.CONTINUE)

    def on_update_prompt_dialog_update_finished(
        self, message: UpdatePromptDialog.UpdateFinished
    ) -> None:
        self.exit(
            UpdatePromptResult.UPDATED
            if message.succeeded
            else UpdatePromptResult.UPDATE_FAILED
        )


def ask_update_prompt(
    current_version: str, latest_version: str, theme: str | None = None
) -> UpdatePromptResult:
    app = UpdatePromptApp(current_version, latest_version, theme=theme)
    return app.run(inline=True) or UpdatePromptResult.CONTINUE
