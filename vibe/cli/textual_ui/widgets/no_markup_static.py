from __future__ import annotations

from typing import Any

from textual.visual import VisualType
from textual.widgets import Static


class NoMarkupStatic(Static):
    def __init__(self, content: VisualType = "", **kwargs: Any) -> None:
        super().__init__(content, markup=False, **kwargs)
