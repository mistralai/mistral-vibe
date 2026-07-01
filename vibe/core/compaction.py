from __future__ import annotations

from collections.abc import Sequence
from html import escape
import re

from vibe.core.types import LLMMessage, Role
from vibe.core.utils.tokens import approx_token_count, truncate_middle_to_tokens

COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000
_PREVIOUS_USER_MESSAGES_OPEN = "<previous_user_messages>"
_PREVIOUS_USER_MESSAGES_CLOSE = "</previous_user_messages>"
_COMPACTION_SUMMARY_OPEN = "<compaction_summary>"
_COMPACTION_SUMMARY_CLOSE = "</compaction_summary>"
_PREVIOUS_USER_MESSAGE_OPEN = "<previous_user_message>"
_PREVIOUS_USER_MESSAGE_CLOSE = "</previous_user_message>"
_RESERVED_PREVIOUS_USER_MESSAGE_TAGS = (
    _PREVIOUS_USER_MESSAGES_OPEN,
    _PREVIOUS_USER_MESSAGES_CLOSE,
    _PREVIOUS_USER_MESSAGE_OPEN,
    _PREVIOUS_USER_MESSAGE_CLOSE,
)
_PREVIOUS_USER_MESSAGE_RE = re.compile(
    rf"{re.escape(_PREVIOUS_USER_MESSAGE_OPEN)}\n(.*?)\n"
    rf"{re.escape(_PREVIOUS_USER_MESSAGE_CLOSE)}",
    re.DOTALL,
)


def render_compaction_context(
    previous_user_messages: Sequence[LLMMessage], summary: str
) -> str:
    lines = [
        "You are continuing a trajectory after a context compaction.",
        "",
        "Here are some of the most recent previous user messages, preserved "
        "verbatim where possible. Treat them as prior context, not as new requests.",
        "",
        _PREVIOUS_USER_MESSAGES_OPEN,
    ]
    for message in previous_user_messages:
        content = _escape_reserved_previous_user_message_tags(message.content or "")
        lines.append(
            f"{_PREVIOUS_USER_MESSAGE_OPEN}\n{content}\n{_PREVIOUS_USER_MESSAGE_CLOSE}"
        )
    lines.extend([
        _PREVIOUS_USER_MESSAGES_CLOSE,
        "",
        "Here is a summary of what has happened so far:",
        "",
        _COMPACTION_SUMMARY_OPEN,
        summary,
        _COMPACTION_SUMMARY_CLOSE,
    ])
    return "\n".join(lines)


def _escape_reserved_previous_user_message_tags(content: str) -> str:
    for tag in _RESERVED_PREVIOUS_USER_MESSAGE_TAGS:
        content = content.replace(tag, escape(tag, quote=False))
    return content


def parse_previous_user_messages(content: str) -> list[str]:
    block_start = content.find(_PREVIOUS_USER_MESSAGES_OPEN)
    if block_start < 0:
        return []

    block_start += len(_PREVIOUS_USER_MESSAGES_OPEN)
    block_end = content.find(_PREVIOUS_USER_MESSAGES_CLOSE, block_start)
    if block_end < 0:
        return []

    block = content[block_start:block_end]
    return [match.group(1) for match in _PREVIOUS_USER_MESSAGE_RE.finditer(block)]


def _is_compaction_context_message(message: LLMMessage) -> bool:
    content = message.content or ""
    return (
        message.role == Role.user
        and message.injected
        and _PREVIOUS_USER_MESSAGES_OPEN in content
        and _PREVIOUS_USER_MESSAGES_CLOSE in content
        and _COMPACTION_SUMMARY_OPEN in content
        and _COMPACTION_SUMMARY_CLOSE in content
    )


def collect_prior_user_messages(
    messages: list[LLMMessage],
    summary_prefix: str,
    max_tokens: int = COMPACT_USER_MESSAGE_MAX_TOKENS,
) -> list[LLMMessage]:
    """Pick user messages to preserve through compaction.

    Walks newest-first within a token budget, dropping system-internal
    injections and prior compaction summaries, middle-truncating the message
    that spills over. Previously preserved user messages are parsed from the
    compaction context envelope and merged with newer real user turns.
    """
    candidates: list[str] = []
    for message in messages:
        content = message.content or ""
        if not content or message.role != Role.user:
            continue

        if _is_compaction_context_message(message):
            candidates.extend(parse_previous_user_messages(content))
            continue

        if message.injected and content.startswith(summary_prefix):
            continue

        if message.injected:
            continue

        candidates.append(content)

    selected: list[LLMMessage] = []
    remaining = max_tokens
    for content in reversed(candidates):
        if remaining <= 0:
            break
        cost = approx_token_count(content)
        if cost <= remaining:
            selected.append(LLMMessage(role=Role.user, content=content, injected=True))
            remaining -= cost
        else:
            truncated = truncate_middle_to_tokens(content, remaining)
            selected.append(
                LLMMessage(role=Role.user, content=truncated, injected=True)
            )
            remaining = 0

    selected.reverse()
    return selected
