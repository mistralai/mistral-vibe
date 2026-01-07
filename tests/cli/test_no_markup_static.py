from __future__ import annotations

import pytest
from textual.markup import MarkupError
from textual.visual import visualize
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


def test_static_raises_on_invalid_markup() -> None:
    widget = Static()
    with pytest.raises(MarkupError):
        visualize(widget, "[/]", markup=True)


def test_no_markup_static_allows_invalid_markup() -> None:
    widget = NoMarkupStatic("[/]")
    assert str(widget.render()) == "[/]"
