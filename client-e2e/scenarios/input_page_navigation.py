from __future__ import annotations

from e2e.app_server.scenario import Timeline

_PAGE_UP = "\x1b[5~"
_PAGE_DOWN = "\x1b[6~"

timeline: Timeline = [
    "\n".join(f"line {index}" for index in range(24)),
    _PAGE_UP,
    "X",
    _PAGE_DOWN,
    "Y",
]
