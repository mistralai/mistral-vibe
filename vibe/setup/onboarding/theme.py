from __future__ import annotations

from vibe.config_values import AUTO_THEME, DEFAULT_THEME
from vibe.observability.logging import logger
from vibe.setup.onboarding.theme_detection import (
    resolve_auto_theme as resolve_auto_theme,
    resolve_theme as resolve_theme,
)


def resolve_theme_name(value: object) -> str:
    from textual.theme import BUILTIN_THEMES

    if value == AUTO_THEME:
        return AUTO_THEME
    if not isinstance(value, str) or value not in BUILTIN_THEMES:
        logger.warning("Unknown theme=%s; falling back to %s", value, DEFAULT_THEME)
        return DEFAULT_THEME
    return value


def sorted_theme_names() -> list[str]:
    from textual.theme import BUILTIN_THEMES

    light = sorted(name for name, t in BUILTIN_THEMES.items() if not t.dark)
    dark = sorted(name for name, t in BUILTIN_THEMES.items() if t.dark)
    return [AUTO_THEME] + light + dark
