from __future__ import annotations

import pytest
from textual.theme import BUILTIN_THEMES

from vibe.cli.theme import resolve_theme_name
from vibe.config_values import DEFAULT_THEME


def test_resolve_theme_name_preserves_a_textual_theme() -> None:
    theme = next(iter(BUILTIN_THEMES))

    assert resolve_theme_name(theme) == theme


@pytest.mark.parametrize("value", [None, "unknown-theme"])
def test_resolve_theme_name_falls_back_for_unsupported_values(value: object) -> None:
    assert resolve_theme_name(value) == DEFAULT_THEME
