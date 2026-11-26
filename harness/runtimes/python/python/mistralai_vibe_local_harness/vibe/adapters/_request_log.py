from __future__ import annotations

import logging

from mistralai_vibe_local_harness.vibe.completion_profile import (
    ReasoningSetting,
    WireFormat,
    reasoning_wire_value,
)

logger = logging.getLogger(__name__)


def log_completion_request(
    provider: str, model: str, wire: WireFormat, reasoning: ReasoningSetting | None
) -> None:
    sent = None if reasoning is None else reasoning_wire_value(wire, reasoning)
    logger.info(
        "Completion request: provider=%s model=%s reasoning=%s sent=%r",
        provider,
        model,
        reasoning,
        sent,
    )
