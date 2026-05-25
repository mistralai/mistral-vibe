from __future__ import annotations

from vibe.core.types import ToolStreamEvent


class ToolTerminalOpenedEvent(ToolStreamEvent):
    message: str = ""
    terminal_id: str
