"""A soft band crosses a copied selection, tinted toward the accent, then the selection settles back."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance, take_snapshot
from scenarios.text_selection._copy_light import (
    COPIED,
    ENV,
    REPLY,
    SELECT,
    mistral_dark,
)

env = ENV
handshake = mistral_dark()

timeline: Timeline = [
    *REPLY,
    SELECT,
    take_snapshot("copied"),
    advance(100),
    take_snapshot("100ms"),
    advance(100),
    take_snapshot("200ms"),
    advance(100),
    take_snapshot("300ms"),
    advance(200),
    take_snapshot("500ms"),
    advance(400),
    take_snapshot("900ms"),
]
clipboard_contains = (COPIED,)
