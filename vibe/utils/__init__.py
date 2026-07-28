from __future__ import annotations

from typing import Literal

from vibe.utils.tool_presentation import ToolEffectKind

type AgentEntrypoint = Literal["cli", "acp", "programmatic", "unknown"]

__all__ = ["AgentEntrypoint", "ToolEffectKind"]
