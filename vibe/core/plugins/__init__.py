"""vibe/core/plugins/__init__.py

─────────────────────────────────────────────────────────────────────────────
Public re-exports for the Vibe plugin system.
"""
from __future__ import annotations

from vibe.core.plugins.base import (
    PluginContext,
    PluginMetadata,
    ToolEventPlugin,
    VibePlugin,
)
from vibe.core.plugins.manager import PluginManager
from vibe.core.plugins.middleware import PluginMiddleware

__all__ = [
    "PluginContext",
    "PluginManager",
    "PluginMetadata",
    "PluginMiddleware",
    "ToolEventPlugin",
    "VibePlugin",
]
