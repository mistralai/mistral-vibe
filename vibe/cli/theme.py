from __future__ import annotations

from textual.theme import BUILTIN_THEMES

from vibe.cli._theme_detection import (
    resolve_auto_theme as resolve_auto_theme,
    resolve_theme as resolve_theme,
)
from vibe.config_values import AUTO_THEME, DEFAULT_THEME
from vibe.observability.logging import logger


def resolve_theme_name(value: object) -> str:
    if value == AUTO_THEME:
        return AUTO_THEME
    if not isinstance(value, str) or value not in BUILTIN_THEMES:
        logger.warning("Unknown theme=%s; falling back to %s", value, DEFAULT_THEME)
        return DEFAULT_THEME
    return value
