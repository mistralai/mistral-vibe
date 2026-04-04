"""vibe/core/plugins/builtin/lsp/plugin.py

─────────────────────────────────────────────────────────────────────────────
LspPlugin — the main built-in plugin that provides LSP integration.

Responsibilities
────────────────
1. On setup():
   • Detect which languages are present in the workdir.
   • Start one LspClient per detected language (if the server is available).
   • Register the LSP tools (lsp_diagnostics, lsp_hover, etc.) into Vibe's
     ToolManager so they appear in the agent's tool list.

2. On on_tool_call() / on_tool_result():
   • Inspect the arguments of file-access tools (read_file, write_file,
     search_replace, grep, bash) to extract the file path being touched.
   • If a new language is detected (file extension not yet seen), start the
     corresponding LSP on-demand.
   • After write_file / search_replace, automatically run lsp_diagnostics
     and inject the result as a tool-result annotation so the agent is
     immediately aware of any errors it introduced.

3. On teardown():
   • Stop all LSP clients gracefully.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from vibe.core.plugins.base import PluginContext, PluginMetadata, ToolEventPlugin
from vibe.core.plugins.builtin.lsp.lsp_client import LspClient, LspClientError
from vibe.core.plugins.builtin.lsp.registry import (
    LSP_REGISTRY,
    detect_languages_in_dir,
    language_for_path,
)
from vibe.core.plugins.builtin.lsp.tools import make_lsp_tools

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Tools that write to files — trigger automatic post-write diagnostics.
_WRITE_TOOLS = frozenset({"write_file", "search_replace"})

# Tools that read files — trigger on-demand LSP startup.
_READ_TOOLS = frozenset({"read_file", "write_file", "search_replace", "grep"})

# Regex to extract file paths from bash commands (heuristic).
_BASH_FILE_RE = re.compile(
    r"""(?:^|\s)(['"]?)([^\s'"]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|java))\1""",
    re.MULTILINE,
)


class LspPlugin(ToolEventPlugin):
    """Built-in Vibe plugin that integrates Language Server Protocol servers.

    Detected and started automatically when Vibe runs in a project that
    contains Python, TypeScript/JS, or Java files.
    """

    @classmethod
    def metadata(cls) -> PluginMetadata:
        return PluginMetadata(
            name="lsp",
            version="1.0.0",
            description=(
                "Integrates LSP servers (pylsp, typescript-language-server, jdtls) "
                "and exposes lsp_diagnostics, lsp_hover, lsp_completion, "
                "lsp_definition, and lsp_references tools to the agent."
            ),
            provides_tools=[
                "lsp_diagnostics",
                "lsp_completion",
                "lsp_hover",
                "lsp_definition",
                "lsp_references",
                "lsp_status",
            ],
        )

    def __init__(self) -> None:
        self._clients: dict[str, LspClient] = {}
        self._detected_languages: set[str] = set()
        self._context: PluginContext | None = None
        # Tracks files that were modified and need post-write diagnostics
        self._pending_diag_files: list[str] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def setup(self, context: PluginContext) -> None:
        self._context = context
        workdir = context.workdir

        # 1. Detect languages present in the project
        self._detected_languages = detect_languages_in_dir(str(workdir))
        logger.info(
            "LSP plugin: detected languages in %s: %s",
            workdir,
            self._detected_languages or "(none)",
        )

        # 2. Start clients for each detected language (non-blocking)
        if self._detected_languages:
            tasks = [
                self._start_client(lang, workdir) for lang in self._detected_languages
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

        # 3. Register LSP tools into Vibe's ToolManager (even if clients not ready yet)
        self._register_tools(context)

    async def teardown(self) -> None:
        tasks = [client.stop() for client in self._clients.values()]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()
        logger.info("LSP plugin: all clients stopped")

    def is_applicable(self, context: PluginContext) -> bool:
        """Active only when at least one supported language is present."""
        langs = detect_languages_in_dir(str(context.workdir), max_files=1000)
        return bool(langs)

    # ── ToolEventPlugin hooks ─────────────────────────────────────────────────

    async def on_tool_call(
        self, tool_name: str, arguments: dict[str, Any], context: PluginContext
    ) -> None:
        """Intercepts tool calls to detect files being accessed.

        • For file-access tools: starts the LSP on demand if needed.
        • Records write targets for post-result diagnostics.
        """
        file_path = self._extract_path(tool_name, arguments)
        if file_path:
            await self._ensure_client_for_file(file_path, context.workdir)
            if tool_name in _WRITE_TOOLS:
                self._pending_diag_files.append(file_path)

    async def on_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        context: PluginContext,
    ) -> None:
        """After a write tool completes, run LSP diagnostics automatically.

        The formatted output is stored in ``context.extra["lsp_diagnostics_output"]``
        as a plain string.  PluginMiddleware._wrapped() picks it up and appends
        it directly to the tool result that flows back to AgentLoop — so both
        the LLM *and* the user terminal see the errors immediately, without any
        additional changes to agent_loop.py.

        Format emitted to the user / LLM::

            ┌─ LSP diagnostics: src/main.py ──────────────────────────────┐
            │ ✗ ERROR   line 12 col 5  — Undefined name 'fob'  [pylsp]    │
            │ ⚠ WARNING line 20 col 1  — Unused import 'os'   [pylsp]     │
            └─ 1 error, 1 warning ────────────────────────────────────────┘
        """
        if tool_name not in _WRITE_TOOLS:
            self._pending_diag_files.clear()
            return

        all_diag_lines: list[str] = []

        for file_path in self._pending_diag_files:
            client = self._client_for_file(file_path)
            if client is None:
                continue
            try:
                # Ensure the client is actually started before calling diagnostics
                if not client.is_running:
                    await client.start()
                diags = await client.diagnostics(file_path)
            except Exception as exc:
                logger.debug("Auto-diagnostics failed for %s: %s", file_path, exc)
                continue

            if not diags:
                continue

            all_diag_lines.append(_format_diagnostics_block(file_path, diags))

        self._pending_diag_files.clear()

        if all_diag_lines:
            output = "\n" + "\n".join(all_diag_lines)
            # PluginMiddleware._wrapped() reads this key and appends the value
            # to the tool result string before returning it to AgentLoop.
            context.extra["lsp_diagnostics_output"] = output
            logger.debug(
                "LSP diagnostics output prepared for %d file(s)", len(all_diag_lines)
            )

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _start_client(self, lang: str, workdir: Path) -> None:
        cfg = LSP_REGISTRY.get(lang)
        if cfg is None:
            return
        if not cfg.is_available():
            logger.warning(
                "LSP for '%s' not available (%s not in PATH). "
                "Install it to enable LSP support for this language.",
                lang,
                cfg.command[0],
            )
            return
        client = LspClient(cfg, workdir)
        try:
            # Start client in background to avoid blocking TUI initialization
            asyncio.create_task(client.start())
            self._clients[lang] = client
        except LspClientError as exc:
            logger.warning("LSP start failed for '%s': %s", lang, exc)
        except Exception as exc:
            logger.exception("Unexpected error starting LSP for '%s': %s", lang, exc)

    async def _ensure_client_for_file(self, file_path: str, workdir: Path) -> None:
        """Start the LSP for a language if we encounter a file of that type."""
        lang = language_for_path(file_path)
        if lang and lang not in self._clients:
            self._detected_languages.add(lang)
            await self._start_client(lang, workdir)
            # Re-register tools to include any newly started client
            if self._context:
                self._register_tools(self._context)

    def _client_for_file(self, file_path: str) -> LspClient | None:
        lang = language_for_path(file_path)
        return self._clients.get(lang) if lang else None

    def _register_tools(self, context: PluginContext) -> None:
        """Inject LSP tools into Vibe's ToolManager."""
        try:
            from vibe.core.tools.manager import ToolManager  # type: ignore[import]

            tm = ToolManager(lambda: context.config)

            # BaseTool.__init__(config, state) — retrieve both from the manager
            # so we pass the exact same objects Vibe uses for all built-in tools.
            vibe_config = tm._config  # Access private _config property
            # For LSP tools, we use a simple empty object as state since LSP tools
            # don't need persistent state management through ToolManager
            from dataclasses import dataclass

            @dataclass
            class EmptyState:
                pass

            vibe_state = EmptyState()
        except Exception as exc:
            logger.warning(
                "LSP plugin could not access ToolManager (config/state): %s. "
                "Tools will not be registered.",
                exc,
            )
            return

        tools = make_lsp_tools(
            config=vibe_config,
            state=vibe_state,
            clients=self._clients,
            detected_languages=self._detected_languages,
            workdir=str(context.workdir),
        )
        try:
            for tool in tools:
                tm.register_dynamic_tool(type(tool))
            logger.debug("LSP plugin: registered %d tools into ToolManager", len(tools))
        except Exception as exc:
            logger.warning(
                "LSP plugin could not register tools into ToolManager: %s.", exc
            )

    @staticmethod
    def _extract_path(tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Extract a file path from tool arguments."""
        # Standard argument names
        for key in ("path", "file_path", "filename"):
            val = arguments.get(key)
            if isinstance(val, str) and val:
                return val
        # Heuristic for bash commands
        if tool_name == "bash":
            cmd = arguments.get("command", "")
            if isinstance(cmd, str):
                m = _BASH_FILE_RE.search(cmd)
                if m:
                    return m.group(2)
        return None


# ── Formatting ────────────────────────────────────────────────────────────────

_SEVERITY_ICON: dict[str, str] = {
    "Error": "✗ ERROR  ",
    "Warning": "⚠ WARNING",
    "Information": "ℹ INFO   ",
    "Hint": "· HINT   ",
}
_WIDTH = 72


def _format_diagnostics_block(file_path: str, diags: list[dict[str, Any]]) -> str:
    """Render a diagnostics list as a bordered block visible in the terminal::

        ┌─ LSP diagnostics: src/main.py ──────────────────────────────────┐
        │ ✗ ERROR   line  12 col  5  Undefined name 'fob'         [pylsp] │
        │ ⚠ WARNING line  20 col  1  Unused import 'os'           [pylsp] │
        └─ 1 error, 1 warning ────────────────────────────────────────────┘

    Errors come first, then warnings, then lower severities.
    This string is appended to the tool result by PluginMiddleware._wrapped()
    so it appears in both the terminal output and the LLM's context window.
    """
    _ORDER = {"Error": 0, "Warning": 1, "Information": 2, "Hint": 3}
    sorted_diags = sorted(diags, key=lambda d: _ORDER.get(d.get("severity", "Hint"), 9))

    header_label = f" LSP diagnostics: {file_path} "
    header = "┌─" + header_label + "─" * max(0, _WIDTH - 2 - len(header_label)) + "┐"

    rows: list[str] = []
    for d in sorted_diags:
        sev = d.get("severity", "Hint")
        icon = _SEVERITY_ICON.get(sev, "? UNKNOWN")
        line_no = d.get("line", "?")
        col_no = d.get("col", "?")
        msg = d.get("message", "")
        source = d.get("source", "")
        source_tag = f"[{source}]" if source else ""
        core = f"line {line_no:>4} col {col_no:>3}  {msg}"
        available = _WIDTH - 4 - len(icon) - 2 - len(source_tag)
        if len(core) > available:
            core = core[: available - 1] + "…"
        pad_len = max(0, available - len(core))
        rows.append(f"│ {icon}  {core}{' ' * pad_len}  {source_tag} │")

    n_errors = sum(1 for d in diags if d.get("severity") == "Error")
    n_warnings = sum(1 for d in diags if d.get("severity") == "Warning")
    other = len(diags) - n_errors - n_warnings
    parts: list[str] = []
    if n_errors:
        parts.append(f"{n_errors} error{'s' if n_errors > 1 else ''}")
    if n_warnings:
        parts.append(f"{n_warnings} warning{'s' if n_warnings > 1 else ''}")
    if other:
        parts.append(f"{other} other")
    summary = " " + ", ".join(parts) + " " if parts else " 0 issues "
    footer = "└─" + summary + "─" * max(0, _WIDTH - 2 - len(summary)) + "┘"

    return "\n".join([header, *rows, footer])
