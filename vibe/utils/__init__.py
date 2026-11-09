from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from vibe.utils.tool_presentation import ToolEffectKind

type AgentEntrypoint = Literal["cli", "acp", "desktop", "programmatic", "unknown"]
VIBE_WARNING_TAG = "vibe_warning"

__all__ = ["VIBE_WARNING_TAG", "AgentEntrypoint", "ToolEffectKind"]


def __getattr__(name: str) -> object:
    if name == "ToolEffectKind":
        from vibe.utils.tool_presentation import ToolEffectKind

        return ToolEffectKind
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
