from __future__ import annotations

from typing import Literal

from vibe.utils.tool_presentation import ToolEffectKind

type AgentEntrypoint = Literal["cli", "acp", "programmatic", "unknown"]
VIBE_WARNING_TAG = "vibe_warning"

__all__ = ["VIBE_WARNING_TAG", "AgentEntrypoint", "ToolEffectKind"]
