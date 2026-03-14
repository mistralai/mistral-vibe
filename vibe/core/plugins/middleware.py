"""vibe/core/plugins/middleware.py

─────────────────────────────────────────────────────────────────────────────
PluginMiddleware — bridges Vibe's MiddlewarePipeline with the plugin system.

How it works
────────────
Vibe's AgentLoop owns a MiddlewarePipeline.  Each middleware can:
  • inject text into the user message before the LLM sees it  (pre_turn)
  • perform cleanup after a turn ends                          (post_turn)

PluginMiddleware intercepts the tool call/result events that flow through
AgentLoop and dispatches them to every registered ToolEventPlugin.

The interception is done by wrapping the AgentLoop's internal
``_execute_tool`` coroutine with a thin decorator that:
  1. calls ``on_tool_call`` BEFORE the real execution
  2. runs the real tool
  3. calls ``on_tool_result`` AFTER the real execution
  4. checks ``context.extra["lsp_diagnostics_output"]`` — if the LSP plugin
     stored a formatted diagnostics string there, it is appended directly to
     the tool result returned to AgentLoop.  This means:
       • The LLM sees the errors in the same message as the tool result.
       • The terminal output includes the errors without any change to
         agent_loop.py.

Wiring example (done once in vibe/core/agent_loop.py or startup code)
─────────────────────────────────────────────────────────────────────
    plugin_mw = PluginMiddleware(plugin_manager)
    agent_loop.middleware_pipeline.add(plugin_mw)
    plugin_mw.patch_agent_loop(agent_loop)
"""

from __future__ import annotations

import functools
import logging
from typing import TYPE_CHECKING, Any

from vibe.core.middleware import ConversationContext, MiddlewareResult
from vibe.core.plugins.base import PluginContext
from vibe.core.tools.base import InvokeContext

if TYPE_CHECKING:
    from vibe.core.agent_loop import AgentLoop  # type: ignore[import]
    from vibe.core.plugins.manager import PluginManager

logger = logging.getLogger(__name__)

# Key used by LspPlugin to pass the formatted diagnostics string back to the
# middleware wrapper so it can be appended to the tool result.
_DIAG_OUTPUT_KEY = "lsp_diagnostics_output"


class PluginMiddleware:
    """Middleware that dispatches tool call / result events to plugins and ensures LSP diagnostics
    are surfaced directly in tool results.

    Parameters
    ----------
    plugin_manager:
        The live :class:`~vibe.core.plugins.manager.PluginManager`.
    context:
        Shared :class:`~vibe.core.plugins.base.PluginContext`.
    """

    def __init__(self, plugin_manager: PluginManager, context: PluginContext) -> None:
        self._manager = plugin_manager
        self._context = context
        self._patched_loop: AgentLoop | None = None

    # ── MiddlewarePipeline interface ──────────────────────────────────────────

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        """Called by the middleware pipeline before each agent turn.

        This delegates to pre_turn which allows plugins to inject text into
        the user message before the LLM sees it.
        """
        # Note: context is not used here but kept for interface compatibility
        return MiddlewareResult()

    async def pre_turn(self, message: str) -> str | None:
        """Called before each agent turn.

        Returns None to leave the message unchanged.
        """
        return None

    async def post_turn(self) -> None:
        """Called after each agent turn completes (or errors out)."""

    def reset(self, reset_reason: str = "stop") -> None:
        """Reset the middleware state and notify all plugins to reset."""
        for plugin in self._manager.tool_event_plugins:
            if hasattr(plugin, "reset") and callable(plugin.reset):
                try:
                    plugin.reset(reset_reason)
                except Exception:
                    logger.exception(
                        "Plugin %s raised in reset()", plugin.metadata().name
                    )

    # ── AgentLoop patching ────────────────────────────────────────────────────

    def patch_agent_loop(self, loop: AgentLoop) -> None:
        """Monkey-patch ``loop._execute_tool`` to intercept every tool execution.

        The wrapper:
          • fires plugin hooks before/after the real tool call
          • appends any LSP diagnostics output directly to the returned
            result string, so both the LLM and the terminal see it

        Safe to call multiple times; the loop is only patched once.
        """
        if self._patched_loop is loop:
            return  # already patched

        original = loop._execute_tool  # type: ignore[attr-defined]
        context = self._context

        @functools.wraps(original)
        async def _wrapped(
            tool_name: str,
            arguments: dict[str, Any],
            ctx: InvokeContext | None = None,
            **kwargs,
        ) -> str:
            # Clear any stale diagnostics output from a previous call
            context.extra.pop(_DIAG_OUTPUT_KEY, None)

            await self._dispatch_on_tool_call(tool_name, arguments)
            result: str = await original(tool_name, arguments, **kwargs)
            await self._dispatch_on_tool_result(tool_name, arguments, result)

            # ── Diagnostics surface-up ────────────────────────────────────────
            # LspPlugin writes a ready-to-display string into context.extra
            # under _DIAG_OUTPUT_KEY.  We append it here so it flows back to
            # AgentLoop as part of the tool result — visible to both the LLM
            # (which uses the result to decide next steps) and to the user
            # (who sees it rendered in the terminal alongside the tool output).
            diag_output: str | None = context.extra.pop(_DIAG_OUTPUT_KEY, None)
            if diag_output:
                result += diag_output

            return result

        loop._execute_tool = _wrapped  # type: ignore[attr-defined]
        self._patched_loop = loop
        logger.debug("PluginMiddleware patched AgentLoop._execute_tool")

    # ── Dispatchers ───────────────────────────────────────────────────────────

    async def _dispatch_on_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> None:
        for plugin in self._manager.tool_event_plugins:
            try:
                await plugin.on_tool_call(tool_name, arguments, self._context)
            except Exception:
                logger.exception(
                    "Plugin %s raised in on_tool_call(%s)",
                    plugin.metadata().name,
                    tool_name,
                )

    async def _dispatch_on_tool_result(
        self, tool_name: str, arguments: dict[str, Any], result: str
    ) -> None:
        for plugin in self._manager.tool_event_plugins:
            try:
                await plugin.on_tool_result(tool_name, arguments, result, self._context)
            except Exception:
                logger.exception(
                    "Plugin %s raised in on_tool_result(%s)",
                    plugin.metadata().name,
                    tool_name,
                )
