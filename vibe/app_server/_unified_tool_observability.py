"""Low-cardinality telemetry for Unified tool presentation projection."""

from __future__ import annotations

from typing import Literal

from opentelemetry import metrics

from vibe.app_server._unified_tool_projection import UnifiedToolCategory

type UnifiedToolProjectionOutcome = Literal["degraded", "projected"]

_meter = metrics.get_meter(__name__)
_projection_count = _meter.create_counter(
    "mistral_ai.vibe.unified_tool.presentation.projection",
    unit="{projection}",
    description="Unified built-in tool presentation projections by bounded outcome.",
)


def add_unified_tool_projection(
    *, category: UnifiedToolCategory, outcome: UnifiedToolProjectionOutcome
) -> None:
    _projection_count.add(
        1,
        {
            "mistral_ai.vibe.unified_tool.category": category,
            "mistral_ai.vibe.unified_tool.outcome": outcome,
        },
    )


__all__ = ["add_unified_tool_projection"]
