"""With the selection animation off, a copied selection stays plain: no band crosses it."""

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
handshake = mistral_dark(disableSelectionAnimation=True)

timeline: Timeline = [*REPLY, SELECT, advance(200), take_snapshot("200ms")]
clipboard_contains = (COPIED,)
