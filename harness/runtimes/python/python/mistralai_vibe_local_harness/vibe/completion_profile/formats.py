# Twin: mistralai.vibe.harness.runtime.completion.profile.formats. Keep both copies identical below this line.
"""Per-format reply tables: finish reasons, context overflow, and token usage.

Every adapter of a format reads a reply through the same tables, so the hosts agree
on what a reply means. A full context window ends as ``length``, never as a normal
stop. A prompt over the window fails as a context overflow, recognised by its error
code or wording. A reply that leaves out its input or output count is unusable:
an unknown count is never read as 0.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .types import WireFormat

type FinishReason = Literal["stop", "tool_call", "length", "content_filter", "other"]

FINISH_REASONS: Mapping[WireFormat, Mapping[str, FinishReason]] = MappingProxyType(
    {
        WireFormat.MISTRAL: MappingProxyType(
            {
                "stop": "stop",
                "tool_calls": "tool_call",
                "length": "length",
                # The model reached its context window while answering.
                "model_length": "length",
            }
        ),
        WireFormat.OPENAI_CHAT: MappingProxyType(
            {
                "stop": "stop",
                "tool_calls": "tool_call",
                "function_call": "tool_call",
                "length": "length",
                "content_filter": "content_filter",
            }
        ),
        # A finished response reads as ``completed``; an incomplete one by its
        # ``incomplete_details.reason``.
        WireFormat.OPENAI_RESPONSES: MappingProxyType(
            {
                "completed": "stop",
                "max_output_tokens": "length",
                "content_filter": "content_filter",
            }
        ),
        WireFormat.ANTHROPIC: MappingProxyType(
            {
                "end_turn": "stop",
                "stop_sequence": "stop",
                "tool_use": "tool_call",
                "max_tokens": "length",
                # The answer reached the context window.
                "model_context_window_exceeded": "length",
            }
        ),
        WireFormat.OLLAMA: MappingProxyType({"stop": "stop", "length": "length"}),
    }
)


def finish_reason(wire: WireFormat, raw: str | None) -> FinishReason:
    """Map a provider's finish or stop reason; one the table lacks is ``other``."""
    if raw is None:
        return "other"
    return FINISH_REASONS[wire].get(raw, "other")


# Error codes and wordings that mean the prompt is over the context window, in
# lower case: OpenAI's code, Anthropic's and Mistral's messages, and the codes of
# servers that answer with a 422.
CONTEXT_OVERFLOW_MARKERS: tuple[str, ...] = (
    "context_length_exceeded",
    "context too long",
    "maximum context length",
    "exceeds the context window",
    "input too large",
    "couldn't fit with truncation",
    "prompt is too long",
    "model_context_exceeded",
    "prompt_too_long",
)

_OVERFLOW_STATUSES = frozenset({400, 413, 422})


def is_context_overflow(status: int | None, body: str | None) -> bool:
    """Say whether a failed reply means the prompt is over the context window.

    ``status`` is the HTTP status, or None for an error event inside a stream.
    """
    if body is None or (status is not None and status not in _OVERFLOW_STATUSES):
        return False
    text = body.lower()
    return any(marker in text for marker in CONTEXT_OVERFLOW_MARKERS)


@dataclass(frozen=True, slots=True)
class UsageFields:
    """Where a format reports its token counts, as key paths into its usage object.

    ``extra_input`` counts belong to the input too: Anthropic reports cached and
    cache-written tokens apart from ``input_tokens``.
    """

    input: tuple[str, ...]
    output: tuple[str, ...]
    cached_input: tuple[str, ...] | None = None
    extra_input: tuple[tuple[str, ...], ...] = ()
    cache_write: tuple[str, ...] | None = None
    reasoning: tuple[str, ...] | None = None


USAGE_FIELDS: Mapping[WireFormat, UsageFields] = MappingProxyType(
    {
        WireFormat.MISTRAL: UsageFields(
            ("prompt_tokens",),
            ("completion_tokens",),
            ("prompt_tokens_details", "cached_tokens"),
            reasoning=("completion_tokens_details", "reasoning_tokens"),
        ),
        WireFormat.OPENAI_CHAT: UsageFields(
            ("prompt_tokens",),
            ("completion_tokens",),
            ("prompt_tokens_details", "cached_tokens"),
            reasoning=("completion_tokens_details", "reasoning_tokens"),
        ),
        WireFormat.OPENAI_RESPONSES: UsageFields(
            ("input_tokens",),
            ("output_tokens",),
            ("input_tokens_details", "cached_tokens"),
            cache_write=("input_tokens_details", "cache_write_tokens"),
            reasoning=("output_tokens_details", "reasoning_tokens"),
        ),
        WireFormat.ANTHROPIC: UsageFields(
            ("input_tokens",),
            ("output_tokens",),
            ("cache_read_input_tokens",),
            (("cache_read_input_tokens",), ("cache_creation_input_tokens",)),
            cache_write=("cache_creation_input_tokens",),
        ),
        WireFormat.OLLAMA: UsageFields(("prompt_eval_count",), ("eval_count",)),
    }
)


# A reply with no usage at all, most often from an OpenAI-compatible server
# streaming without the usage chunk.
NO_USAGE_MESSAGE = (
    "the server returned no token usage; an OpenAI-compatible server must support "
    "stream_options.include_usage"
)


class MissingUsageError(ValueError):
    """A reply left out its input or output token count."""


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """One reply's token counts; the cached and cache-written input are part of the input."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """The input and output counts together."""
        return self.input_tokens + self.output_tokens


def _count(usage: Mapping[str, object], path: tuple[str, ...]) -> int | None:
    value: object = usage
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)  # pyright: ignore[reportUnknownMemberType]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def read_usage(wire: WireFormat, usage: Mapping[str, object] | None) -> TokenCounts:
    """Read a format's usage object; raise ``MissingUsageError`` without both counts.

    A missing cache or reasoning count is 0: those counts are optional, the input
    and output are not.
    """
    fields = USAGE_FIELDS[wire]
    if usage is None:
        raise MissingUsageError(NO_USAGE_MESSAGE)
    input_tokens = _count(usage, fields.input)
    output_tokens = _count(usage, fields.output)
    missing = [
        name
        for name, value in (("input", input_tokens), ("output", output_tokens))
        if value is None
    ]
    if input_tokens is None or output_tokens is None:
        raise MissingUsageError(
            f"the {wire} reply has no {' or '.join(missing)} token count"
        )
    extra = sum(_count(usage, path) or 0 for path in fields.extra_input)
    cached = 0 if fields.cached_input is None else _count(usage, fields.cached_input)
    cache_write = 0 if fields.cache_write is None else _count(usage, fields.cache_write)
    reasoning = 0 if fields.reasoning is None else _count(usage, fields.reasoning)
    return TokenCounts(
        input_tokens=input_tokens + extra,
        output_tokens=output_tokens,
        cached_input_tokens=cached or 0,
        cache_write_tokens=cache_write or 0,
        reasoning_tokens=reasoning or 0,
    )
