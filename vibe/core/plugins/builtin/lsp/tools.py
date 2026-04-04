"""vibe/core/plugins/builtin/lsp/tools.py

─────────────────────────────────────────────────────────────────────────────
LSP tools exposed to the Vibe agent as native BaseTool instances.

These tools are injected into Vibe's ToolManager by LspPlugin.setup() so
the LLM can call them just like any built-in tool (read_file, bash, etc.).

Available tools
───────────────
  lsp_diagnostics   — errors and warnings in a file
  lsp_completion    — completion suggestions at a position
  lsp_hover         — documentation / type of a symbol
  lsp_definition    — location of a symbol's definition
  lsp_references    — all references to a symbol
  lsp_status        — show which LSPs are active

BaseTool contract (inferred from mypy diagnostics on the real Vibe source)
──────────────────────────────────────────────────────────────────────────
  • __init__(self, config: VibeConfig, state)  — two required positional args
  • async run(self, **kwargs) -> str           — abstract, must be implemented
  • get_tool_prompt(self) -> str | None        — @lru_cache on the superclass;
        subclasses must return str | None and must NOT change the signature
  • name, description, input_schema            — class-level attributes
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
import functools
import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from vibe.core.tools.base import BaseTool  # type: ignore[import]
from vibe.core.types import ToolStreamEvent  # type: ignore[import]

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig  # type: ignore[import]
    from vibe.core.plugins.builtin.lsp.lsp_client import LspClient
    from vibe.core.tools.base import InvokeContext

logger = logging.getLogger(__name__)

# Shared docstring fragments (avoids SonarQube S1192 duplication warning)
_LINE_DOC = "1-indexed line number."
_COL_DOC = "1-indexed column number."
_MISSING_PARAMS_MSG = "Missing required parameters: file_path, line, col"


# ── Base class for all LSP tools ──────────────────────────────────────────────


class _LspBaseTool(BaseTool):
    """Shared base for tools that need access to the LSP client pool.

    LSP tools are not constructed directly — use make_lsp_tools() which
    calls BaseTool.__init__(config, state) and then attaches _clients.
    """

    # Attached by make_lsp_tools() after __init__
    _clients: dict[str, LspClient]

    def _client_for(self, file_path: str) -> LspClient | None:
        from vibe.core.plugins.builtin.lsp.registry import language_for_path

        lang = language_for_path(file_path)
        return self._clients.get(lang) if lang else None

    def _no_client_msg(self, file_path: str) -> str:
        from vibe.core.plugins.builtin.lsp.registry import language_for_path

        lang = language_for_path(file_path)
        if lang is None:
            return f"No LSP configured for extension: {file_path!r}"
        return (
            f"LSP for language '{lang}' is not running. "
            f"Check that the server is installed and the plugin is active."
        )


# ── lsp_diagnostics ───────────────────────────────────────────────────────────


class LspDiagnosticsTool(_LspBaseTool):
    name = "lsp_diagnostics"
    description = (
        "Get diagnostics (errors, warnings, hints) for a file from its Language Server. "
        "Use this after writing or modifying a file to verify there are no semantic errors."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to analyse (absolute or relative to workdir).",
            }
        },
        "required": ["file_path"],
    }

    async def run(
        self, args: dict[str, Any], ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | str, None]:
        if not isinstance(args, dict) or "file_path" not in args:
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=_MISSING_PARAMS_MSG,
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args["file_path"]
        client = self._client_for(file_path)
        if client is None:
            yield self._no_client_msg(file_path)
            return
        try:
            diags = await client.diagnostics(file_path)
        except Exception as exc:
            yield f"LSP error: {exc}"
            return
        if not diags:
            yield "✓ No diagnostics — file looks clean."
        else:
            yield json.dumps(diags, indent=2, ensure_ascii=False)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_diagnostics(file_path) after writing or modifying a file to verify "
            "there are no semantic errors."
        )


# ── lsp_completion ────────────────────────────────────────────────────────────


class LspCompletionTool(_LspBaseTool):
    name = "lsp_completion"
    description = (
        "Get code completion suggestions at a position in a file. "
        "Use to explore available methods, properties, or identifiers."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the source file."},
            "line": {"type": "integer", "description": _LINE_DOC},
            "col": {"type": "integer", "description": _COL_DOC},
        },
        "required": ["file_path", "line", "col"],
    }

    async def run(
        self, args: Any, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | str, None]:
        if (
            not isinstance(args, dict)
            or "file_path" not in args
            or "line" not in args
            or "col" not in args
        ):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args["file_path"]
        line = int(args["line"])
        col = int(args["col"])
        client = self._client_for(file_path)
        if client is None:
            yield self._no_client_msg(file_path)
            return
        try:
            items = await client.completion(file_path, line, col)
        except Exception as exc:
            yield f"LSP error: {exc}"
            return
        if not items:
            yield "No completion suggestions at this position."
        else:
            yield json.dumps(items, indent=2, ensure_ascii=False)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_completion(file_path, line, col) to explore available methods, "
            "properties, or identifiers at a specific location."
        )


# ── lsp_hover ─────────────────────────────────────────────────────────────────


class LspHoverTool(_LspBaseTool):
    name = "lsp_hover"
    description = (
        "Get the type signature and documentation of a symbol at a position. "
        "Use before modifying a function or class to understand its contract."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "line": {"type": "integer", "description": _LINE_DOC},
            "col": {"type": "integer", "description": _COL_DOC},
        },
        "required": ["file_path", "line", "col"],
    }

    async def run(
        self, args: Any, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | str, None]:
        if (
            not isinstance(args, dict)
            or "file_path" not in args
            or "line" not in args
            or "col" not in args
        ):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args["file_path"]
        line = int(args["line"])
        col = int(args["col"])
        client = self._client_for(file_path)
        if client is None:
            yield self._no_client_msg(file_path)
            return
        try:
            info = await client.hover(file_path, line, col)
        except Exception as exc:
            yield f"LSP error: {exc}"
            return
        result = info.get("content") or "No hover information at this position."
        yield result

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_hover(file_path, line, col) before modifying a function or class to "
            "understand its contract."
        )


# ── lsp_definition ────────────────────────────────────────────────────────────


class LspDefinitionTool(_LspBaseTool):
    name = "lsp_definition"
    description = (
        "Go to the definition of a symbol. "
        "Returns the file, line and column where the symbol is defined."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "line": {"type": "integer", "description": _LINE_DOC},
            "col": {"type": "integer", "description": _COL_DOC},
        },
        "required": ["file_path", "line", "col"],
    }

    async def run(
        self, args: Any, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | str, None]:
        if (
            not isinstance(args, dict)
            or "file_path" not in args
            or "line" not in args
            or "col" not in args
        ):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args["file_path"]
        line = int(args["line"])
        col = int(args["col"])
        client = self._client_for(file_path)
        if client is None:
            yield self._no_client_msg(file_path)
            return
        try:
            locs = await client.definition(file_path, line, col)
        except Exception as exc:
            yield f"LSP error: {exc}"
            return
        if not locs:
            yield "No definition found."
        else:
            yield json.dumps(locs, indent=2, ensure_ascii=False)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return "Use lsp_definition(file_path, line, col) to navigate to the definition of a symbol."


# ── lsp_references ────────────────────────────────────────────────────────────


class LspReferencesTool(_LspBaseTool):
    name = "lsp_references"
    description = (
        "Find all references to a symbol across the project. "
        "Use before renaming or deleting a symbol to understand its impact."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "line": {"type": "integer", "description": _LINE_DOC},
            "col": {"type": "integer", "description": _COL_DOC},
            "include_declaration": {
                "type": "boolean",
                "default": True,
                "description": "Whether to include the declaration itself in the results.",
            },
        },
        "required": ["file_path", "line", "col"],
    }

    async def run(
        self, args: dict[str, Any], ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | str, None]:
        if (
            not isinstance(args, dict)
            or "file_path" not in args
            or "line" not in args
            or "col" not in args
        ):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=_MISSING_PARAMS_MSG,
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args["file_path"]
        line = int(args["line"])
        col = int(args["col"])
        include_declaration = args.get("include_declaration", True)
        client = self._client_for(file_path)
        if client is None:
            yield self._no_client_msg(file_path)
            return
        try:
            refs = await client.references(
                file_path, line, col, bool(include_declaration)
            )
        except Exception as exc:
            yield f"LSP error: {exc}"
            return
        if not refs:
            yield "No references found."
        else:
            yield json.dumps(refs, indent=2, ensure_ascii=False)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_references(file_path, line, col) before renaming or "
            "removing a symbol to see everywhere it is used."
        )


# ── lsp_status ────────────────────────────────────────────────────────────────


class LspStatusTool(BaseTool):
    name = "lsp_status"
    description = (
        "Show the status of the LSP plugin: which language servers are running "
        "and which languages were detected in the project."
    )
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    # Attached by make_lsp_tools()
    _lsp_clients: dict[str, LspClient]
    _detected_languages: set[str]
    _workdir: str

    async def run(
        self, args: dict[str, Any], ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | str, None]:
        status = {
            "workdir": self._workdir,
            "detected_languages": sorted(self._detected_languages),
            "running_lsp": [
                {"language": lang, "running": client.is_running}
                for lang, client in sorted(self._lsp_clients.items())
            ],
        }
        yield json.dumps(status, indent=2, ensure_ascii=False)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_status() at the start of a coding session to verify "
            "which language servers are available."
        )


# ── Factory ───────────────────────────────────────────────────────────────────


def make_lsp_tools(
    config: VibeConfig,
    state: Any,
    clients: dict[str, LspClient],
    detected_languages: set[str],
    workdir: str,
) -> list[BaseTool]:
    """Instantiate all LSP tools using the real BaseTool.__init__ signature
    (config, state) and then attach LSP-specific attributes.

    Parameters
    ----------
    config:
        The live VibeConfig instance — forwarded to BaseTool.__init__.
    state:
        The agent state object — forwarded to BaseTool.__init__.
    clients:
        Map of language name -> running LspClient.
    detected_languages:
        Set of language names detected in the project.
    workdir:
        Absolute path to the project root as a string.
    """
    file_tool_classes: list[type[_LspBaseTool]] = [
        LspDiagnosticsTool,
        LspCompletionTool,
        LspHoverTool,
        LspDefinitionTool,
        LspReferencesTool,
    ]
    tools: list[BaseTool] = []

    for cls in file_tool_classes:
        tool = cls(config, state)
        tool._clients = clients  # type: ignore[attr-defined]
        tools.append(tool)

    status_tool = LspStatusTool(config, state)
    status_tool._lsp_clients = clients  # type: ignore[attr-defined]
    status_tool._detected_languages = detected_languages  # type: ignore[attr-defined]
    status_tool._workdir = workdir  # type: ignore[attr-defined]
    tools.append(status_tool)

    return tools
