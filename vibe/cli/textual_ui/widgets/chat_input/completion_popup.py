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
        self._selected_index: int = -1

    def update_suggestions(
        self, suggestions: list[tuple[str, str]], selected: int
    ) -> None:
        if not suggestions:
            self.hide()
            return

        if suggestions != self._suggestions:
            rows = self._rebuild(suggestions)
            self._select_all(rows, selected)
        else:
            rows = list(self.query(_CompletionRow))
            self._select_incremental(rows, selected)
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

    def _select_all(self, rows: list[_CompletionRow], selected: int) -> None:
        """Update all row classes (used when rebuilding popup)."""
        for idx, row in enumerate(rows):
            row.set_class(idx == selected, SELECTED_CLASS)
        if 0 <= selected < len(rows):
            rows[selected].scroll_visible(animate=False)
        self._selected_index = selected

    def _select_incremental(self, rows: list[_CompletionRow], selected: int) -> None:
        """Update only changed row classes (used when just moving selection)."""
        if selected == self._selected_index:
            return

        # Deselect old row
        if 0 <= self._selected_index < len(rows):
            rows[self._selected_index].set_class(False, SELECTED_CLASS)

        # Select new row
        if 0 <= selected < len(rows):
            rows[selected].set_class(True, SELECTED_CLASS)
            rows[selected].scroll_visible(animate=False)

        self._selected_index = selected

    def hide(self) -> None:
        self.remove_children()
        self._suggestions = []
        self._selected_index = -1
        self.styles.display = "none"

    @property
    def content_text(self) -> str:
        return "\n".join(str(child.render()) for child in self.query(_CompletionItem))

    @staticmethod
    def _display_label(label: str) -> str:
        if label.startswith("@"):
            return label[1:]
        return label
