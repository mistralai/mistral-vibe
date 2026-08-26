from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"
PLUGIN_PLACEHOLDER = "<plugin>"

_INPUT_VALUE = re.compile(
    r"input_value=.+?(?=(?:, input_type=)|$)", re.DOTALL | re.MULTILINE
)


def redact_names(values: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(values))


def redact_values(values: Mapping[str, str]) -> dict[str, str]:
    return {name: REDACTED for name in sorted(values)}


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def sanitize_message(message: str, roots: Iterable[Path] = ()) -> str:
    sanitized = _INPUT_VALUE.sub(f"input_value={REDACTED}", message)
    candidates = sorted(
        (str(root) for root in roots), key=lambda value: len(value), reverse=True
    )
    for root in candidates:
        sanitized = sanitized.replace(root, PLUGIN_PLACEHOLDER)
    return sanitized
