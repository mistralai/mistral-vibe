from __future__ import annotations

from rich.markup import escape
from textual.content import Content

SHORTCUT_STYLE = "b not dim $primary"


def shortcut(key: str) -> str:
    return f"[{SHORTCUT_STYLE}]{escape(key)}[/]"


def shortcut_hint(markup: str) -> Content:
    return Content.from_markup(markup)
