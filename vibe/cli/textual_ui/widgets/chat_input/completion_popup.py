from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static


class CompletionPopup(Static):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", id="completion-popup", **kwargs)
        self.styles.display = "none"
        self.can_focus = False

    def update_suggestions(
        self, suggestions: list[tuple[str, str]], selected: int
    ) -> None:
        if not suggestions:
            self.hide()
            return

        text = Text()
        for idx, (label, description) in enumerate(suggestions):
            if idx:
                text.append("\n")

            label_style = "bold reverse" if idx == selected else "bold"
            description_style = "italic" if idx == selected else "dim"

            text.append(label, style=label_style)
            if description:
                text.append("  ")
                text.append(description, style=description_style)

        self.update(text)
        self.show()

    def hide(self) -> None:
        self.update("")
        self.styles.display = "none"

    def show(self) -> None:
        self.styles.display = "block"
