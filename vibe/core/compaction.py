from __future__ import annotations

from vibe.core.types import LLMMessage, Role
from vibe.core.utils.tokens import approx_token_count, truncate_middle_to_tokens

COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000


def collect_prior_user_messages(
    messages: list[LLMMessage],
    summary_prefix: str,
    max_tokens: int = COMPACT_USER_MESSAGE_MAX_TOKENS,
) -> list[LLMMessage]:
    """Pick user messages to preserve through compaction.

    Walks newest-first within a token budget, dropping system-internal
    injections and prior compaction summaries, middle-truncating the message
    that spills over. Returns kept messages in chronological order.
    """
    candidates = [
        m
        for m in messages
        if m.role == Role.user
        and not m.injected
        and m.content
        and not m.content.startswith(summary_prefix)
    ]

    selected: list[LLMMessage] = []
    remaining = max_tokens
    for m in reversed(candidates):
        if remaining <= 0:
            break
        content = m.content or ""
        cost = approx_token_count(content)
        if cost <= remaining:
            selected.append(LLMMessage(role=Role.user, content=content))
            remaining -= cost
        else:
            truncated = truncate_middle_to_tokens(content, remaining)
            selected.append(LLMMessage(role=Role.user, content=truncated))
            remaining = 0

    selected.reverse()
    return selected
