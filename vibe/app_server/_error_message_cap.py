from __future__ import annotations

import re

# Clients paint an error message whole, so it stays a few rows tall. Provider
# errors can echo the request: a rejected image comes back as its full data URI.
ERROR_MESSAGE_MAX_CHARS = 2_000
ERROR_TRUNCATION_MARKER = "… [truncated]"

_DATA_URI = re.compile(
    r"(data:[\w.+-]+/[\w.+-]+(?:;[\w.+-]+=[\w.+-]+)*;base64,)([A-Za-z0-9+/]+=*)"
)


def bounded_error_message(message: str) -> str:
    redacted = _DATA_URI.sub(
        lambda match: f"{match[1]}<{len(match[2])} base64 chars omitted>", message
    )
    # The marker's allowance keeps capping idempotent across projections.
    if len(redacted) <= ERROR_MESSAGE_MAX_CHARS + len(ERROR_TRUNCATION_MARKER):
        return redacted
    return f"{redacted[:ERROR_MESSAGE_MAX_CHARS]}{ERROR_TRUNCATION_MARKER}"
