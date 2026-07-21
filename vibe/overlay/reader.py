from __future__ import annotations

import sys

from PySide6.QtCore import QThread, Signal

from vibe.core.pawgress.events import IslandState, parse_island_state


def _try_parse(line: str) -> IslandState | None:
    line = line.strip()
    if not line:
        return None
    try:
        return parse_island_state(line)
    except ValueError:
        return None


class StdinReader(QThread):
    """Demo-only reader: reads scripted island states from stdin (stub_feed.py)."""

    received = Signal(object)

    def run(self) -> None:
        for line in sys.stdin:
            state = _try_parse(line)
            if state is not None:
                self.received.emit(state)
