"""vibe/core/plugins/extension_points.py

────────────────────────────────────────────────────────────────────────────
Pluggy extension point specifications for plugin hooks.

This module defines the hook specifications that plugins can implement
using the pluggy library. Hook specifications provide a way to extend
Vibe's functionality at key points in the execution pipeline.

Usage::

    from vibe.core.plugins.extension_points import HookSpecs, HookspecMarker

    # Register hooks in your plugin
    hookimpl = HookspecMarker("mistral-vibe")

    @hookimpl
    def on_tool_call(tool_name, arguments, context):
        # Handle tool call event
        pass

    # Alternatively, use class-based hooks
    class MyPlugin:
        @hookimpl
        def on_tool_call(self, tool_name, arguments, context):
            ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pluggy

if TYPE_CHECKING:
    type VibeSession = object


# ──────────────────────────────────────────────────────────────────────────────
# Hook Markers
# ──────────────────────────────────────────────────────────────────────────────

HookspecMarker = pluggy.HookspecMarker("mistral-vibe")
HookImplMarker = pluggy.HookimplMarker("mistral-vibe")


# ──────────────────────────────────────────────────────────────────────────────
# Hook Specifications
# ──────────────────────────────────────────────────────────────────────────────


class HookSpecs:
    """Hook specifications for Mistral Vibe plugin system.

    Plugins implement these hooks to extend Vibe's behavior at key
    points in the agent lifecycle and execution pipeline.
    """

    @staticmethod
    @HookspecMarker
    def on_tool_call(
        tool_name: str,
        arguments: dict[str, Any],
        context: PluginContext,
    ) -> None:
        """Called before a tool is executed.

        This hook is invoked just before the agent executes a tool.
        Plugins can inspect or modify tool arguments, log the call,
        or perform preprocessing.

        Parameters
        ----------
        tool_name:
            The canonical name of the tool (e.g., "read_file").
        arguments:
            Raw arguments dict as sent by the LLM.
        context:
            Current plugin context including workdir and config.
        """

    @staticmethod
    @HookspecMarker
    def on_tool_result(
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        context: PluginContext,
    ) -> None:
        """Called after a tool has executed and produced a result.

        This hook is invoked after tool execution completes. Plugins
        can inspect the result, log it, or trigger follow-up actions.

        Parameters
        ----------
        tool_name:
            The canonical name of the tool.
        arguments:
            Raw arguments dict that was used for the call.
        result:
            The string result returned by the tool.
        context:
            Current plugin context including workdir and config.
        """

    @staticmethod
    @HookspecMarker
    def on_session_start(session: VibeSession) -> None:
        """Called when a new Vibe session starts.

        This hook is invoked when the agent begins a new session,
        either from scratch or by loading an existing session.
        Plugins can initialize session-specific state here.

        Parameters
        ----------
        session:
            The newly created or loaded VibeSession instance.
        """

    @staticmethod
    @HookspecMarker
    def on_session_end(session: VibeSession) -> None:
        """Called when a Vibe session ends.

        This hook is invoked when the session is shutting down.
        Plugins should clean up any session-specific resources here.

        Parameters
        ----------
        session:
            The VibeSession instance that is ending.
        """

    @staticmethod
    @HookspecMarker
    def register_commands(registry: CommandRegistry) -> None:
        """Called to register slash commands.

        This hook allows plugins to register custom slash commands
        that will be available in the CLI. Commands are invoked
        with a leading slash (e.g., /mycommand).

        Parameters
        ----------
        registry:
            Registry object with methods to register commands.
        """

    @staticmethod
    @HookspecMarker
    def get_tools() -> list[type] | None:
        """Called to contribute additional tools.

        This hook allows plugins to contribute custom tools that will
        be registered with the ToolManager and available to the agent.

        Returns
        -------
        list[type] | None
            List of Tool subclass types to register, or None.
        """
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Type aliases for documentation
# ──────────────────────────────────────────────────────────────────────────────

if TYPE_CHECKING:
    from dataclasses import dataclass

    @dataclass
    class PluginContext:
        """Minimal context passed to hook methods.

        This is a stub for type hints. The actual PluginContext
        is defined in base.py.
        """

        pass

    class CommandRegistry:
        """Registry for slash commands.

        This is a stub for type hints. The actual implementation
        provides methods to register commands.
        """

        def register(self, name: str, callback: Any) -> None:
            """Register a command callback."""
            ...