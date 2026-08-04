from __future__ import annotations

import math

_APPROX_BYTES_PER_TOKEN = 4
_TRUNCATION_MARKER = "\n\n[... truncated ...]\n\n"


def approx_token_count(text: str) -> int:
    return math.ceil(len(text) / _APPROX_BYTES_PER_TOKEN)


def truncate_middle_to_tokens(text: str, max_tokens: int) -> str:
    """Shrink ``text`` to fit in ``max_tokens`` by dropping the middle.

    Keeps head + tail (intent and constraints usually live at the ends of user
    messages) and inserts a marker where the middle was removed.
    """
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * _APPROX_BYTES_PER_TOKEN
    if len(text) <= max_chars:
        return text
    available = max_chars - len(_TRUNCATION_MARKER)
    if available <= 0:
        return text[:max_chars]
    head = available // 2
    tail = available - head
    return text[:head] + _TRUNCATION_MARKER + text[-tail:]
