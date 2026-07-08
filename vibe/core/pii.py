from __future__ import annotations

import re
from typing import Any

# Regex to match absolute paths (including ones with spaces in interior segments).
# Check tests/test_pii.py::test_scrub_paths
_PATH_RE = re.compile(
    r"(?<![\w\\/])(?:[A-Za-z]:[\\/]|~?[\\/])"
    r"(?:[^\s\"'`\\/]+(?:[ ]+[^\s\"'`\\/]+)*[\\/])+"
    r"([^\s\"'`\\/]+)"
)

# Filter the username in a bare home dir (last segment); the lookahead bails only when a
# real deeper segment follows, so _PATH_RE handles those instead.
_HOME_RE = re.compile(
    r"(?<![\w\\/])(?:[A-Za-z]:[\\/]|~?[\\/])(?:Users|home)[\\/]"
    r"[^\s\"'`\\/]++(?!(?:[ ]+[^\s\"'`\\/]+)*+[\\/][^\s\"'`\\/])"
)

_FILTERED = "[Filtered]"


def scrub_paths(value: Any) -> Any:
    """Scrub absolute paths from a string, or recursively from a dict, list, or tuple."""
    match value:
        case str():
            return _PATH_RE.sub(rf"{_FILTERED}/\1", _HOME_RE.sub(_FILTERED, value))
        case dict():
            return {key: scrub_paths(item) for key, item in value.items()}
        case list():
            return [scrub_paths(item) for item in value]
        case tuple():
            return tuple(scrub_paths(item) for item in value)
        case _:
            return value
