from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.widgets import OptionList


class NavigableOptionList(OptionList):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]
