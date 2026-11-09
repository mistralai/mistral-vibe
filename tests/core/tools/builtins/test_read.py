from __future__ import annotations

from vibe.core.tools.builtins.read_file import DEFAULT_LINE_LIMIT


def test_default_limit_is_2000() -> None:
    assert DEFAULT_LINE_LIMIT == 2000
