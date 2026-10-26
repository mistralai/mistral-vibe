"""A reply's token usage, read through its format's usage table.

Every adapter reads usage here, so a count the provider left out is never read
as 0: a reply without its input or output count fails, and only the cache and
reasoning counts are optional.
"""

from __future__ import annotations

from collections.abc import Mapping

from mistralai_vibe_local_harness.protocol import RustTokenUsage
from mistralai_vibe_local_harness.vibe.adapters._provider_failure import (
    MissingTokenUsage,
)
from mistralai_vibe_local_harness.vibe.completion_profile import (
    MissingUsageError,
    WireFormat,
    read_usage,
)


def reply_token_usage(wire: WireFormat, raw_usage: object) -> RustTokenUsage:
    """Read a reply's usage, or fail when it lacks the input or output count."""
    try:
        counts = read_usage(wire, _usage_mapping(raw_usage))
    except MissingUsageError as error:
        raise MissingTokenUsage(f"The provider reply is unusable: {error}.") from error
    return RustTokenUsage(
        input_tokens=counts.input_tokens,
        output_tokens=counts.output_tokens,
        total_tokens=counts.total_tokens,
        cached_input_tokens=counts.cached_input_tokens,
        cache_write_tokens=counts.cache_write_tokens,
        reasoning_tokens=counts.reasoning_tokens,
    )


def has_reply_usage(wire: WireFormat, raw_usage: object) -> bool:
    """Whether the usage reported so far holds the input and output counts."""
    try:
        read_usage(wire, _usage_mapping(raw_usage))
    except MissingUsageError:
        return False
    return True


def merge_reported_usage(
    current: dict[str, object] | None, update: Mapping[str, object]
) -> dict[str, object]:
    """Fold one more usage report of a stream into the ones before it.

    A later count wins; a count the later report leaves out keeps its earlier
    value, as when Anthropic sends the input count only at the start.
    """
    merged = dict(current or {})
    merged.update({key: value for key, value in update.items() if value is not None})
    return merged


def _usage_mapping(raw_usage: object) -> Mapping[str, object] | None:
    if raw_usage is None or isinstance(raw_usage, Mapping):
        return raw_usage  # pyright: ignore[reportUnknownVariableType]
    dump = getattr(raw_usage, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, Mapping):
            return dumped  # pyright: ignore[reportUnknownVariableType]
    raise TypeError(f"unreadable provider usage: {type(raw_usage).__name__}")


__all__ = ["has_reply_usage", "merge_reported_usage", "reply_token_usage"]
