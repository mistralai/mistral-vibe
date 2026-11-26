"""Match a completion's finish reason to the tool calls it carries.

Core accepts a ``tool_call`` finish only with at least one tool call part, and
tool call parts only with a ``tool_call`` finish. A provider's finish reason can
disagree with what it sent: it can finish with tool calls without sending any,
send tool calls with another finish reason, or send a tool call without a name,
which cannot be run and is dropped. Every adapter reconciles the two here, so
they cannot disagree about the rule, and records each repair it makes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from mistralai_vibe_local_harness.protocol import RustCompletionFinishReason
from mistralai_vibe_local_harness.vibe._runtime_config import MalformedToolCallReason
from mistralai_vibe_local_harness.vibe.adapters._provider_failure import (
    UnusableToolCalls,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CompletionSource:
    """The provider and model that produced a completion."""

    provider: str
    model: str


@dataclass(slots=True)
class MalformedToolCalls:
    """The repairs made to the responses of one completion, across its attempts."""

    reasons: set[MalformedToolCallReason] = field(default_factory=set)
    unusable_attempts: int = 0
    dropped_tool_calls: int = 0

    def __bool__(self) -> bool:
        return bool(self.reasons)


def align_finish_reason(
    finish_reason: RustCompletionFinishReason | None,
    *,
    kept_tool_calls: int,
    dropped_tool_calls: int,
    source: CompletionSource,
    record: MalformedToolCalls | None = None,
) -> RustCompletionFinishReason:
    """Return the finish reason Core accepts for the tool calls kept.

    ``finish_reason`` is ``None`` when the provider reported none.
    ``dropped_tool_calls`` counts the calls the provider sent without a name.
    When they are all the provider sent, this raises ``UnusableToolCalls`` so
    the retry policy makes another attempt. A ``length`` finish is kept even
    with tool calls: a call cut off by the output limit lets Core discard it
    and ask again. A tool call finish that came with no tool call at all
    becomes ``stop`` instead: a provider that does this every time would fail
    every attempt. Each repair is added to ``record``.
    """
    record = record if record is not None else MalformedToolCalls()
    if dropped_tool_calls:
        logger.warning(
            "Provider %s (%s) sent %d tool call(s) without a name; dropping them",
            source.provider,
            source.model,
            dropped_tool_calls,
        )
        record.reasons.add("empty_tool_call_identity")
        record.dropped_tool_calls += dropped_tool_calls
    if kept_tool_calls:
        if finish_reason == "length":
            # A call cut off by the output limit keeps `length` so Core can
            # discard it and ask again.
            return "length"
        if finish_reason is not None and finish_reason != "tool_call":
            # Some OpenAI-compatible servers do this on every tool call.
            logger.info(
                "Provider %s (%s) sent tool calls with the %r finish reason; "
                "reporting 'tool_call'",
                source.provider,
                source.model,
                finish_reason,
            )
            record.reasons.add("tool_call_with_non_tool_call_finish")
        return "tool_call"
    if dropped_tool_calls:
        record.unusable_attempts += 1
        raise UnusableToolCalls(
            f"Model response from {source.provider} ({source.model}) contained "
            "only tool calls without a name."
        )
    if finish_reason != "tool_call":
        return finish_reason or "stop"
    logger.warning(
        "Provider %s (%s) finished with tool calls but sent none; reporting 'stop'",
        source.provider,
        source.model,
    )
    record.reasons.add("tool_call_finish_without_tool_call")
    return "stop"
