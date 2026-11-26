"""Ctrl+S swaps the draft with the stash, and a restored draft keeps its undo history."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_S = "\x13"
_UNDO = "\x1b[122;9u"

screen_contains = ("> first", "Ctrl+S restore stash")
screen_excludes = ("first draft", "second draft")

timeline: Timeline = [
    "first",
    # Stash "first", then Ctrl+S on the empty input gives it back.
    _CTRL_S,
    _CTRL_S,
    " draft",
    _CTRL_S,
    # A typed draft trades places with the stash.
    "second draft",
    _CTRL_S,
    # Undo takes back " draft": the restored draft brought its history.
    _UNDO,
]
