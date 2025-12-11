"""ChefChat Mode Types
====================

Core type definitions for the mode system.
Extracted to break circular dependencies between mode_manager and mode_errors.

This module contains:
- VibeMode enum: The five operational modes
- ModeConfig: Configuration dataclass for each mode
- ModeState: Runtime state tracker with history
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

try:
    from vibe.core.compatibility import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass


# =============================================================================
# ENUMS
# =============================================================================


class VibeMode(StrEnum):
    """The five operational modes of ChefChat.

    Each mode changes tool execution permissions, communication style,
    and system prompt behavior.
    """

    PLAN = "plan"  # 📋 Research & Planning - Read-only
    NORMAL = "normal"  # ✋ Safe & Steady - Ask for each tool
    AUTO = "auto"  # ⚡ Trust & Execute - Auto-approve all
    YOLO = "yolo"  # 🚀 Move Fast - Maximum speed, minimal output
    ARCHITECT = "architect"  # 🏛️ Design Mode - High-level, read-only


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class ModeConfig:
    """Configuration for a single mode."""

    auto_approve: bool
    read_only: bool
    emoji: str
    description: str
    personality: str


@dataclass
class ModeState:
    """Tracks the current mode state and history.

    Attributes:
        current_mode: The active operational mode
        auto_approve: Whether tools are auto-approved
        read_only_tools: Whether write operations are blocked
        started_at: When this mode was activated
        mode_history: Log of mode transitions with timestamps
    """

    current_mode: VibeMode
    auto_approve: bool = False
    read_only_tools: bool = True
    started_at: datetime = field(default_factory=datetime.now)
    mode_history: list[tuple[VibeMode, datetime]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize from mode config.

        Note: This will be called after MODE_CONFIGS is populated.
        """
        # Import here to avoid circular dependency
        from vibe.modes.constants import MODE_CONFIGS

        config = MODE_CONFIGS[self.current_mode]
        self.auto_approve = config.auto_approve
        self.read_only_tools = config.read_only
        if not self.mode_history:
            self.mode_history = [(self.current_mode, self.started_at)]

    def to_dict(self) -> dict[str, Any]:
        """Serialize state for logging/debugging."""
        return {
            "mode": self.current_mode.value,
            "auto_approve": self.auto_approve,
            "read_only": self.read_only_tools,
            "started_at": self.started_at.isoformat(),
            "transitions": len(self.mode_history),
        }
