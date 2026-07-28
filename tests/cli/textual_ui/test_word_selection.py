from __future__ import annotations

from textual.geometry import Offset
from textual.widget import Widget

from vibe.cli.textual_ui.word_selection import WordSelectScreen


def test_get_widget_and_offset_at_drops_detached_widget(monkeypatch):
    screen = WordSelectScreen(id="_default")
    detached = Widget()
    assert detached.parent is None

    monkeypatch.setattr(
        screen._compositor,
        "get_widget_and_offset_at",
        lambda x, y: (detached, Offset(1, 1)),
    )

    assert screen.get_widget_and_offset_at(0, 0) == (None, None)


def test_get_widget_and_offset_at_keeps_mounted_widget(monkeypatch):
    screen = WordSelectScreen(id="_default")
    parent = Widget()
    child = Widget()
    child._parent = parent
    offset = Offset(2, 3)

    monkeypatch.setattr(
        screen._compositor, "get_widget_and_offset_at", lambda x, y: (child, offset)
    )

    assert screen.get_widget_and_offset_at(0, 0) == (child, offset)
