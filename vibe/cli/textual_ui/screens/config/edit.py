from __future__ import annotations

import json
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, TextArea
from textual.widgets.option_list import Option

from vibe.app_server.protocol import ConfigFieldKind, ConfigFieldWire
from vibe.cli.textual_ui.screens.config._common import (
    CONFIG_EDIT_SCREEN_ID,
    EditResult,
    apply_stacked_width,
    inspector_text,
    target_bar_text,
)
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput


class _TargetedEditScreen(ModalScreen[EditResult | None]):
    """Base edit modal with a read-only layer inspector and a save-target toggle.

    Tab cycles the target between the ephemeral session override and the
    persisted TOML layer; the chosen target rides back on ``EditResult``.
    """

    SCOPED_CSS = False
    CSS_PATH = "edit.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("tab", "toggle_target", "Switch target", show=False, priority=True),
    ]

    def __init__(self, *, view: ConfigFieldWire, targets: tuple[str, ...]) -> None:
        super().__init__(id=CONFIG_EDIT_SCREEN_ID)
        self._view = view
        self._layer_values = view.layer_values
        self._targets = targets
        self._target = targets[0]

    def _compose_side(self) -> ComposeResult:
        with Vertical(id="config-edit-side"):
            yield NoMarkupStatic("WHERE IT'S SET", classes="config-edit-side-header")
            yield NoMarkupStatic(
                inspector_text(self._layer_values, self._view.value_labels),
                classes="config-edit-inspector",
            )

    def _compose_save_bar(self) -> ComposeResult:
        yield NoMarkupStatic(
            target_bar_text(self._target, self._targets),
            id="config-edit-target",
            classes="config-edit-target",
        )

    def _help_line(self, text: str) -> NoMarkupStatic:
        return NoMarkupStatic(shortcut_hint(text), classes="config-edit-help")

    def _persistence_hint(self) -> str:
        if len(self._targets) <= 1:
            return ""
        return f"  {shortcut('Tab')} Change Layer"

    def _set_title(self) -> None:
        self.query_one("#config-edit-content").border_title = self._view.name

    def action_toggle_target(self) -> None:
        if len(self._targets) <= 1:
            return
        index = self._targets.index(self._target)
        self._target = self._targets[(index + 1) % len(self._targets)]
        self.query_one("#config-edit-target", NoMarkupStatic).update(
            target_bar_text(self._target, self._targets)
        )

    def _finish(self, value: Any) -> None:
        self.dismiss((value, self._target))

    def action_cancel(self) -> None:
        self.dismiss(None)


class _ChoiceEditScreen(_TargetedEditScreen):
    def compose(self) -> ComposeResult:
        with Vertical(id="config-edit-content"):
            with Horizontal(id="config-edit-body"):
                with Vertical(id="config-edit-main"):
                    if self._view.description:
                        yield NoMarkupStatic(
                            self._view.description, classes="config-edit-description"
                        )
                    yield NavigableOptionList(
                        *[
                            Option(
                                self._view.value_labels.get(choice, choice), id=choice
                            )
                            for choice in self._view.enum_choices
                        ],
                        id="config-edit-options",
                    )
                yield from self._compose_side()
            yield from self._compose_save_bar()
            yield self._help_line(
                f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Select  "
                f"{shortcut('Esc')} Cancel{self._persistence_hint()}"
            )

    def on_mount(self) -> None:
        self._set_title()
        apply_stacked_width(self, self.query_one("#config-edit-content"))
        option_list = self.query_one(OptionList)
        option_list.focus()
        current = str(self._view.value)
        for index, choice in enumerate(self._view.enum_choices):
            if choice == current:
                option_list.highlighted = index
                return

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            self.dismiss(None)
            return
        self._finish(str(event.option.id))


class ValueEditScreen(_TargetedEditScreen):
    def __init__(
        self,
        *,
        view: ConfigFieldWire,
        initial: str,
        multiline: bool,
        targets: tuple[str, ...],
    ) -> None:
        super().__init__(view=view, targets=targets)
        self._initial = initial
        self._multiline = multiline

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+s", "save", "Save", show=False)
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="config-edit-content"):
            with Horizontal(id="config-edit-body"):
                with Vertical(id="config-edit-main"):
                    if self._view.description:
                        yield NoMarkupStatic(
                            self._view.description, classes="config-edit-description"
                        )
                    if self._multiline:
                        yield NoMarkupStatic(
                            (
                                "One item per line."
                                if self._view.kind is ConfigFieldKind.LIST
                                else "Edit as JSON."
                            ),
                            classes="config-edit-description",
                        )
                        yield TextArea(self._initial, id="config-edit-textarea")
                    else:
                        yield VscodeCompatInput(
                            value=self._initial, id="config-edit-input"
                        )
                yield from self._compose_side()
            yield from self._compose_save_bar()
            hint = "Ctrl+S Save" if self._multiline else "Enter Save"
            yield self._help_line(
                f"{shortcut(hint)}  {shortcut('Esc')} Cancel{self._persistence_hint()}"
            )

    def on_mount(self) -> None:
        self._set_title()
        apply_stacked_width(self, self.query_one("#config-edit-content"))
        if self._multiline:
            self.query_one(TextArea).focus()
        else:
            self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._finish(event.value)

    def action_save(self) -> None:
        if self._multiline:
            self._finish(self.query_one(TextArea).text)


async def prompt_field_value(
    app: App[Any], view: ConfigFieldWire, targets: tuple[str, ...]
) -> EditResult | None:
    if not targets:
        return None
    if view.kind in {ConfigFieldKind.BOOL, ConfigFieldKind.ENUM}:
        choices = ["True", "False"] if view.kind is ConfigFieldKind.BOOL else None
        result = await app.push_screen_wait(
            _ChoiceEditScreen(
                view=view.model_copy(update={"enum_choices": choices})
                if choices is not None
                else view,
                targets=targets,
            )
        )
        if result is None or view.kind is ConfigFieldKind.ENUM:
            return result
        value, target = result
        return value == "True", target

    multiline = view.kind in {ConfigFieldKind.LIST, ConfigFieldKind.COMPLEX}
    if view.kind is ConfigFieldKind.LIST and isinstance(view.value, list):
        initial = "\n".join(str(item) for item in view.value)
    elif view.kind is ConfigFieldKind.COMPLEX:
        initial = json.dumps(view.value, indent=2, ensure_ascii=False)
    else:
        initial = "" if view.value is None else str(view.value)
    result = await app.push_screen_wait(
        ValueEditScreen(
            view=view, initial=initial, multiline=multiline, targets=targets
        )
    )
    if result is None:
        return None
    raw, target = result
    try:
        match view.kind:
            case ConfigFieldKind.INT:
                value = int(raw.strip())
            case ConfigFieldKind.FLOAT:
                value = float(raw.strip())
            case ConfigFieldKind.LIST:
                value = [line.strip() for line in raw.splitlines() if line.strip()]
            case ConfigFieldKind.COMPLEX:
                value = json.loads(raw)
            case _:
                value = raw
    except (json.JSONDecodeError, ValueError) as exc:
        app.notify(f"Invalid value: {exc}", severity="error", markup=False)
        return None
    return value, target


__all__ = ["ValueEditScreen", "prompt_field_value"]
