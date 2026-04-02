from __future__ import annotations

from fnmatch import fnmatch
import functools
import re


@functools.lru_cache(maxsize=256)
def _compile_icase(expr: str) -> re.Pattern[str] | None:
    try:
        return re.compile(expr, re.IGNORECASE)
    except re.error:
        return None


def name_matches(name: str, patterns: list[str]) -> bool:
    """Check if a name matches any of the provided patterns.

    Supports two forms (case-insensitive):
    - Glob wildcards using fnmatch (e.g., 'serena_*')
    - Regex when prefixed with 're:' (e.g., 're:serena.*')
    """
    n = name.lower()
    for raw in patterns:
        if not (p := (raw or "").strip()):
            continue

        if p.startswith("re:"):
            rx = _compile_icase(p.removeprefix("re:"))
            if rx is not None and rx.fullmatch(name) is not None:
                return True
        elif fnmatch(n, p.lower()):
            return True

    return False
