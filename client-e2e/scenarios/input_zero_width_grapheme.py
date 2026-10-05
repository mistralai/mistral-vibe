"""A zero-width Mongolian vowel separator shares its letter's cell, so the caret never hides on it."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_LEFT = "\x1b[D"
_BACKSPACE = "\x7f"

timeline: Timeline = [paste("ᠸᠠᠭᠢᠨᠳᠠᠷ\u180eᠠ ᠶᠢᠨ"), _LEFT * 5, _BACKSPACE]

settle_per_key = True
