from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["AgentSafety", "AgentType"]


class AgentSafety(StrEnum):
    SAFE = auto()
    NEUTRAL = auto()
    DESTRUCTIVE = auto()
    YOLO = auto()


class AgentType(StrEnum):
    AGENT = auto()
    SUBAGENT = auto()
