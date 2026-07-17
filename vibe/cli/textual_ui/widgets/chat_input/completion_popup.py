from __future__ import annotations

from typing import Any

from rich.cells import cell_len
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static

COMPLETION_POPUP_MAX_HEIGHT = 12
COMPLETION_POPUP_PADDING_X = 1
SELECTED_CLASS = "completion-selected"
NO_DESCRIPTION_CLASS = "completion-no-description"


class _CompletionItem(Static):
    pass


class _CompletionRow(Horizontal):
    pass


class CompletionPopup(VerticalScroll):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="completion-popup", **kwargs)
        self.styles.display = "none"
        self.styles.max_height = COMPLETION_POPUP_MAX_HEIGHT
        self.styles.padding = (0, COMPLETION_POPUP_PADDING_X)
        self.can_focus = False
        self._suggestions: list[tuple[str, str]] = []

    def update_suggestions(
        self, suggestions: list[tuple[str, str]], selected: int
    ) -> None:
        if not suggestions:
            self.hide()
            return

        if suggestions != self._suggestions:
            rows = self._rebuild(suggestions)
        else:
            rows = list(self.query(_CompletionRow))
        self._select(rows, selected)
        self.styles.display = "block"

    def _rebuild(self, suggestions: list[tuple[str, str]]) -> list[_CompletionRow]:
        self.remove_children()
        self._suggestions = suggestions
        has_descriptions = any(description for _, description in suggestions)
        self.set_class(not has_descriptions, NO_DESCRIPTION_CLASS)
        command_width = max(
            cell_len(self._display_label(label)) for label, _ in suggestions
        )
        rows: list[_CompletionRow] = []
        for label, description in suggestions:
            command = _CompletionItem(
                self._display_label(label), classes="completion-command"
            )
            if has_descriptions:
                command.styles.width = command_width
            description_cell = _CompletionItem(
                description, classes="completion-description"
            )
            rows.append(_CompletionRow(command, description_cell))
        self.mount_all(rows)
        return rows

    @staticmethod
    def _select(rows: list[_CompletionRow], selected: int) -> None:
        for idx, row in enumerate(rows):
            row.set_class(idx == selected, SELECTED_CLASS)
        if 0 <= selected < len(rows):
            rows[selected].scroll_visible(animate=False)

    def hide(self) -> None:
        self.remove_children()
        self._suggestions = []
        self.styles.display = "none"

    @property
    def content_text(self) -> str:
        return "\n".join(str(child.render()) for child in self.query(_CompletionItem))

    @staticmethod
    def _display_label(label: str) -> str:
        if label.startswith("@"):
            return label[1:]
        return label
