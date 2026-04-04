"""vibe/core/plugins/base.py

─────────────────────────────────────────────────────────────────────────────
Base interfaces for the Vibe plugin system.

A plugin is a Python package (or single module) that hooks into Vibe's
execution pipeline.  Two hook families are provided:

  • VibePLugin        — lifecycle hooks (startup / shutdown / config)
  • ToolEventPlugin   — reacts to tool calls and their results

Plugins are discovered by PluginManager and activated through
PluginMiddleware, which is inserted into AgentLoop's MiddlewarePipeline.

Usage in a plugin module::

    from vibe.core.plugins.base import ToolEventPlugin, PluginMetadata

    class MyPlugin(ToolEventPlugin):
        @classmethod
        def metadata(cls) -> PluginMetadata:
            return PluginMetadata(name="my-plugin", version="0.1.0",
                                  description="Does something useful")

        async def on_tool_call(self, tool_name: str, arguments: dict,
                               context: "PluginContext") -> None:
            ...  # react before execution

        async def on_tool_result(self, tool_name: str, arguments: dict,
                                 result: str,
                                 context: "PluginContext") -> None:
            ...  # react after execution
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig


# ──────────────────────────────────────────────────────────────────────────────
# Metadata
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginMetadata:
    """Static description of a plugin, used for discovery and display."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    # Extra capabilities advertised by the plugin
    provides_tools: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Context passed to hook methods
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class PluginContext:
    """Snapshot of the current Vibe state made available to plugin hooks.

    Attributes
    ----------
    workdir:
        Absolute path to the current project root (same as
        ``config.effective_workdir``).
    config:
        The live ``VibeConfig`` instance.
    extra:
        Free-form dict that plugins may use to share state across hooks
        within a single agent turn.
    """

    workdir: Path
    config: VibeConfig
    extra: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# Base plugin interface
# ──────────────────────────────────────────────────────────────────────────────


class VibePlugin(abc.ABC):
    """Minimal lifecycle interface that all plugins must implement.

    Plugins are instantiated once by :class:`PluginManager` and kept alive
    for the duration of the Vibe session.
    """

    @classmethod
    @abc.abstractmethod
    def metadata(cls) -> PluginMetadata:
        """Return static metadata for this plugin."""

    @abc.abstractmethod
    async def setup(self, context: PluginContext) -> None:
        """Called once after the plugin is instantiated and the config is ready.

        Use this to start background processes, connect to servers, or
        pre-compute any state that should persist across turns.
        """

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Called when Vibe is shutting down.

        Release all resources acquired in :meth:`setup`.
        """

    def is_applicable(self, context: PluginContext) -> bool:
        """Return True if this plugin should be active for the given context.

        The default implementation always returns True.  Override to enable
        context-aware activation (e.g., only when certain file types are
        present in the workdir).
        """
        return True


# ──────────────────────────────────────────────────────────────────────────────
# Tool-event plugin
# ──────────────────────────────────────────────────────────────────────────────


class ToolEventPlugin(VibePlugin, abc.ABC):
    """Plugin that reacts to tool call / result events emitted by AgentLoop.

    The two hook methods are called **synchronously within the agent turn**
    by :class:`~vibe.core.plugins.middleware.PluginMiddleware`.  They must
    be non-blocking or use asyncio properly.

    Both methods receive the same ``context`` object so plugins can share
    transient per-turn state via ``context.extra``.
    """

    # File-access tools whose ``path`` / ``file_path`` argument tells us
    # which file the agent is looking at.  Subclasses may extend this set.
    FILE_ACCESS_TOOLS: frozenset[str] = frozenset({
        "read_file",
        "write_file",
        "search_replace",
        "grep",
        "ls",
    })

    async def on_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: PluginContext
    ) -> None:
        """Called just *before* a tool is executed.

        Parameters
        ----------
        tool_name:
            The canonical name of the tool (e.g. ``"read_file"``).
        arguments:
            Raw arguments dict as sent by the LLM.
        context:
            Current plugin context.
        """

    async def on_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        context: PluginContext,
    ) -> None:
        """Called just *after* a tool has executed and produced a result.

        Parameters
        ----------
        tool_name:
            The canonical name of the tool.
        arguments:
            Raw arguments dict that was used for the call.
        result:
            The string result returned by the tool.
        context:
            Current plugin context.
        """

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def extract_file_path(tool_name: str, arguments: dict[str, Any]) -> Path | None:
        """Try to extract a file path from tool arguments.

        Returns None when the tool is not file-related or when the argument
        is missing / not a string.
        """
        for key in ("path", "file_path", "filename", "filepath"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                return Path(value)
        return None
