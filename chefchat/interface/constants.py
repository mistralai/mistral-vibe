"""ChefChat TUI Constants - Centralized constants for the interface.

This module provides enums and constants to replace magic strings
throughout the TUI codebase, improving maintainability and type safety.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Final


class StationName(str, Enum):
    """Station identifiers used throughout the TUI."""

    SOUS_CHEF = "sous_chef"
    LINE_COOK = "line_cook"
    SOMMELIER = "sommelier"
    EXPEDITOR = "expeditor"


class BusAction(str, Enum):
    """Actions for bus messages."""

    STATUS_UPDATE = "STATUS_UPDATE"
    LOG_MESSAGE = "LOG_MESSAGE"
    PLATE_CODE = "PLATE_CODE"
    STREAM_UPDATE = "STREAM_UPDATE"
    TERMINAL_LOG = "TERMINAL_LOG"
    NEW_TICKET = "NEW_TICKET"
    PLAN = "PLAN"


class StatusString(str, Enum):
    """Status strings that map to StationStatus."""

    IDLE = "idle"
    PLANNING = "planning"
    COOKING = "cooking"
    TESTING = "testing"
    REFACTORING = "refactoring"
    COMPLETE = "complete"
    ERROR = "error"


class StationStatus(Enum):
    """Status of a kitchen station."""

    IDLE = auto()  # "At ease" - waiting for orders
    WORKING = auto()  # "Firing" - actively cooking
    COMPLETE = auto()  # "Plated" - finished the order
    ERROR = auto()  # "86'd" - something went wrong


class TicketCommand(str, Enum):
    """Slash commands available in the TUI."""

    QUIT = "quit"
    HELP = "help"
    CLEAR = "clear"
    SETTINGS = "settings"
    CHEF = "chef"
    PLATE = "plate"


# Default station configuration - (station_id, display_name)
DEFAULT_STATIONS: Final[list[tuple[str, str]]] = [
    (StationName.SOUS_CHEF.value, "Sous Chef"),
    (StationName.LINE_COOK.value, "Line Cook"),
    (StationName.SOMMELIER.value, "Sommelier"),
    (StationName.EXPEDITOR.value, "Expeditor"),
]

# Whisk animation frames
WHISK_FRAMES: Final[list[str]] = ["   🥄", "  🥄 ", " 🥄  ", "🥄   ", " 🥄  ", "  🥄 "]

# Status emoji mapping
STATUS_EMOJI: Final[dict[str, str]] = {
    StatusString.IDLE.value: "⚪",
    StatusString.PLANNING.value: "📋",
    StatusString.COOKING.value: "🔥",
    StatusString.TESTING.value: "🧪",
    StatusString.REFACTORING.value: "🔧",
    StatusString.COMPLETE.value: "✅",
    StatusString.ERROR.value: "❌",
}

# Ticket type emojis
TICKET_EMOJI: Final[dict[str, str]] = {
    "user": "👨‍🍳",
    "assistant": "🍳",
    "system": "📋",
}


class PayloadKey(str, Enum):
    """Common keys used in bus message payloads."""

    STATION = "station"
    STATUS = "status"
    PROGRESS = "progress"
    MESSAGE = "message"
    TYPE = "type"
    CONTENT = "content"
    CODE = "code"
    LANGUAGE = "language"
    FILE_PATH = "file_path"
    FULL_CONTENT = "full_content"
    TICKET_ID = "ticket_id"
    REQUEST = "request"
    TASK = "task"


class MessageType(str, Enum):
    """Types of messages in the ticket rail/logs."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# Characters to sanitize from markdown input
MARKDOWN_SANITIZE_CHARS: Final[dict[str, str]] = {
    "\x00": "",  # Null bytes
    "\x0b": "",  # Vertical tab
    "\x0c": "",  # Form feed
}
