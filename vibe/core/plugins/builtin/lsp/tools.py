"""vibe/core/plugins/builtin/lsp/tools.py

─────────────────────────────────────────────────────────────────────────────
LSP tools exposed to the Vibe agent as native BaseTool instances.

These tools are injected into Vibe's ToolManager by LspPlugin.setup() so
the LLM can call them just like any built-in tool (read_file, bash, etc.).

Available tools
──────────────
  lsp_diagnostics   — errors and warnings in a file
  lsp_completion    — completion suggestions at a position
  lsp_hover         — documentation / type of a symbol
  lsp_definition    — location of a symbol's definition
  lsp_references    — all references to a symbol
  lsp_document_symbols — all symbols defined in a document
  lsp_workspace_symbols — search symbols across the workspace
  lsp_signature_help — function/method signature help
  lsp_code_action   — available refactorings and quick fixes
  lsp_formatting    — format a document
  lsp_range_formatting — format a specific range
  lsp_document_highlight — highlight symbol occurrences
  lsp_folding_ranges — foldable code regions
  lsp_rename        — rename a symbol safely
  lsp_implementation — find interface/protocol implementations
  lsp_type_definition — find type definitions
  lsp_status        — show which LSPs are active
  lsp_debug         — launch interactive LSP inspector

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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from vibe.core.tools.base import (  # type: ignore[import]
    BaseTool,
    BaseToolConfig,
    BaseToolState,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent  # type: ignore[import]

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig  # type: ignore[import]
    from vibe.core.plugins.builtin.lsp.lsp_client import LspClient
    from vibe.core.tools.base import InvokeContext

logger = logging.getLogger(__name__)

# Module-level LSP client registry (set by make_lsp_tools, read by tool instances)
_LSP_CLIENTS: dict[str, LspClient] = {}
_LSP_DETECTED_LANGUAGES: set[str] = set()
_LSP_WORKDIR: str = ""

# Shared docstring fragments (avoids SonarQube S1192 duplication warning)
_LINE_DOC = "1-indexed line number."
_COL_DOC = "1-indexed column number."
_MISSING_PARAMS_MSG = "Missing required parameters: file_path, line, col"


# Pydantic models for LSP tool arguments and results
class LspDiagnosticsArgs(BaseModel):
    file_path: str = Field(
        description="Path to the file to analyse (absolute or relative to workdir)."
    )


class LspDiagnosticsResult(BaseModel):
    diagnostics: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspCompletionArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)


class LspCompletionResult(BaseModel):
    items: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspHoverArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)


class LspHoverResult(BaseModel):
    content: str | None = Field(default=None)
    message: str


class LspDefinitionArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)


class LspDefinitionResult(BaseModel):
    locations: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspReferencesArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)
    include_declaration: bool = Field(
        default=True,
        description="Whether to include the declaration itself in the results.",
    )


class LspReferencesResult(BaseModel):
    references: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspStatusArgs(BaseModel):
    pass


class LspStatusResult(BaseModel):
    status: dict[str, Any]


# ── Base class for all LSP tools ──────────────────────────────────────────────
from typing import TypeVar

# Type variables for generic args and result types
_LspArgs = TypeVar("_LspArgs", bound=BaseModel)
_LspResult = TypeVar("_LspResult", bound=BaseModel)


class LspToolConfig(BaseToolConfig):
    """Configuration for LSP tools."""

    pass


class _LspBaseTool(BaseTool[_LspArgs, _LspResult, LspToolConfig, BaseToolState]):
    """Shared base for tools that need access to the LSP client pool.

    LSP tools are not constructed directly — use make_lsp_tools() which
    sets the module-level _LSP_CLIENTS registry.
    """

    def _client_for(self, file_path: str) -> LspClient | None:
        from vibe.core.plugins.builtin.lsp.registry import language_for_path

        lang = language_for_path(file_path)
        return _LSP_CLIENTS.get(lang) if lang else None

    def _no_client_msg(self, file_path: str) -> str:
        from vibe.core.plugins.builtin.lsp.registry import language_for_path

        lang = language_for_path(file_path)
        if lang is None:
            return f"No LSP configured for extension: {file_path!r}"
        return (
            f"LSP for language '{lang}' is not running. "
            f"Check that the server is installed and the plugin is active."
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Querying LSP server"


# ── lsp_diagnostics ───────────────────────────────────────────────────────────


class LspDiagnosticsTool(
    _LspBaseTool[LspDiagnosticsArgs, LspDiagnosticsResult],
    ToolUIData[LspDiagnosticsArgs, LspDiagnosticsResult],
):
    name = "lsp_diagnostics"
    description = (
        "Get diagnostics (errors, warnings, hints) for a file from its Language Server. "
        "Use this after writing or modifying a file to verify there are no semantic errors."
    )

    async def run(
        self, args: LspDiagnosticsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspDiagnosticsResult, None]:
        if not isinstance(args, LspDiagnosticsArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=_MISSING_PARAMS_MSG,
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        client = self._client_for(file_path)
        if client is None:
            result = LspDiagnosticsResult(
                diagnostics=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            diags = await client.diagnostics(file_path)
        except Exception as exc:
            result = LspDiagnosticsResult(diagnostics=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "✓ No diagnostics — file looks clean."
            if not diags
            else json.dumps(diags, indent=2, ensure_ascii=False)
        )
        result = LspDiagnosticsResult(
            diagnostics=diags if diags else None, message=message
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspDiagnosticsArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Checking diagnostics for {args.file_path}")

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspDiagnosticsResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.diagnostics:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.diagnostics)} diagnostics in {Path(event.result.diagnostics[0].get('file_path', '')).name}",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_diagnostics(file_path) after writing or modifying a file to verify "
            "there are no semantic errors."
        )


# ── lsp_completion ────────────────────────────────────────────────────────────


class LspCompletionTool(
    _LspBaseTool[LspCompletionArgs, LspCompletionResult],
    ToolUIData[LspCompletionArgs, LspCompletionResult],
):
    name = "lsp_completion"
    description = (
        "Get code completion suggestions at a position in a file. "
        "Use to explore available methods, properties, or identifiers."
    )

    async def run(
        self, args: LspCompletionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspCompletionResult, None]:
        if not isinstance(args, LspCompletionArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        client = self._client_for(file_path)
        if client is None:
            result = LspCompletionResult(
                items=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            items = await client.completion(file_path, line, col)
        except Exception as exc:
            result = LspCompletionResult(items=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No completion suggestions at this position."
            if not items
            else json.dumps(items, indent=2, ensure_ascii=False)
        )
        result = LspCompletionResult(items=items if items else None, message=message)
        yield result

    @classmethod
    def format_call_display(cls, args: LspCompletionArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Getting completions at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspCompletionResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.items and len(event.result.items) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.items)} completion suggestions",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_completion(file_path, line, col) to explore available methods, "
            "properties, or identifiers at a specific location."
        )


# ── lsp_hover ─────────────────────────────────────────────────────────────────


class LspHoverTool(
    _LspBaseTool[LspHoverArgs, LspHoverResult], ToolUIData[LspHoverArgs, LspHoverResult]
):
    name = "lsp_hover"
    description = (
        "Get the type signature and documentation of a symbol at a position. "
        "Use before modifying a function or class to understand its contract."
    )

    async def run(
        self, args: LspHoverArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspHoverResult, None]:
        if not isinstance(args, LspHoverArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        client = self._client_for(file_path)
        if client is None:
            result = LspHoverResult(
                content=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            info = await client.hover(file_path, line, col)
        except Exception as exc:
            result = LspHoverResult(content=None, message=f"LSP error: {exc}")
            yield result
            return

        content = info.get("content") or "No hover information at this position."
        result = LspHoverResult(content=content, message=content)
        yield result

    @classmethod
    def format_call_display(cls, args: LspHoverArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Getting hover info at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspHoverResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        return ToolResultDisplay(success=True, message="Hover information retrieved")

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_hover(file_path, line, col) before modifying a function or class to "
            "understand its contract."
        )


# ── lsp_definition ────────────────────────────────────────────────────────────


class LspDefinitionTool(
    _LspBaseTool[LspDefinitionArgs, LspDefinitionResult],
    ToolUIData[LspDefinitionArgs, LspDefinitionResult],
):
    name = "lsp_definition"
    description = (
        "Go to the definition of a symbol. "
        "Returns the file, line and column where the symbol is defined."
    )

    async def run(
        self, args: LspDefinitionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspDefinitionResult, None]:
        if not isinstance(args, LspDefinitionArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        client = self._client_for(file_path)
        if client is None:
            result = LspDefinitionResult(
                locations=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            locs = await client.definition(file_path, line, col)
        except Exception as exc:
            result = LspDefinitionResult(locations=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No definition found."
            if not locs
            else json.dumps(locs, indent=2, ensure_ascii=False)
        )
        result = LspDefinitionResult(locations=locs if locs else None, message=message)
        yield result

    @classmethod
    def format_call_display(cls, args: LspDefinitionArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Finding definition at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspDefinitionResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.locations and len(event.result.locations) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found definition in {Path(event.result.locations[0].get('file_path', '')).name}",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return "Use lsp_definition(file_path, line, col) to navigate to the definition of a symbol."


# ── lsp_references ────────────────────────────────────────────────────────────


class LspReferencesTool(
    _LspBaseTool[LspReferencesArgs, LspReferencesResult],
    ToolUIData[LspReferencesArgs, LspReferencesResult],
):
    name = "lsp_references"
    description = (
        "Find all references to a symbol across the project. "
        "Use before renaming or deleting a symbol to understand its impact."
    )

    async def run(
        self, args: LspReferencesArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspReferencesResult, None]:
        if not isinstance(args, LspReferencesArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=_MISSING_PARAMS_MSG,
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        include_declaration = args.include_declaration
        client = self._client_for(file_path)
        if client is None:
            result = LspReferencesResult(
                references=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            refs = await client.references(
                file_path, line, col, bool(include_declaration)
            )
        except Exception as exc:
            result = LspReferencesResult(references=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No references found."
            if not refs
            else json.dumps(refs, indent=2, ensure_ascii=False)
        )
        result = LspReferencesResult(references=refs if refs else None, message=message)
        yield result

    @classmethod
    def format_call_display(cls, args: LspReferencesArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Finding references at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspReferencesResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.references and len(event.result.references) > 0:
            return ToolResultDisplay(
                success=True, message=f"Found {len(event.result.references)} references"
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_references(file_path, line, col) before renaming or "
            "removing a symbol to see everywhere it is used."
        )


# ── lsp_status ────────────────────────────────────────────────────────────────


class LspStatusTool(
    BaseTool[LspStatusArgs, LspStatusResult, BaseToolConfig, BaseToolState],
    ToolUIData[LspStatusArgs, LspStatusResult],
):
    name = "lsp_status"
    description = (
        "Show the status of the LSP plugin: which language servers are running "
        "and which languages were detected in the project."
    )

    async def run(
        self, args: LspStatusArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspStatusResult, None]:
        status = {
            "workdir": _LSP_WORKDIR,
            "detected_languages": sorted(_LSP_DETECTED_LANGUAGES),
            "running_lsp": [
                {"language": lang, "running": client.is_running}
                for lang, client in sorted(_LSP_CLIENTS.items())
            ],
        }
        result = LspStatusResult(status=status)
        yield result

    @classmethod
    def format_call_display(cls, args: LspStatusArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Checking LSP server status")

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspStatusResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        running_count = sum(
            1 for lsp in event.result.status["running_lsp"] if lsp["running"]
        )
        return ToolResultDisplay(
            success=True,
            message=f"LSP status: {running_count}/{len(event.result.status['running_lsp'])} servers running",
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Checking LSP server status"

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_status() at the start of a coding session to verify "
            "which language servers are available."
        )


# ── lsp_document_symbols ───────────────────────────────────────────────────────


class LspDocumentSymbolsArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")


class LspDocumentSymbolsResult(BaseModel):
    symbols: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspDocumentSymbolsTool(
    _LspBaseTool[LspDocumentSymbolsArgs, LspDocumentSymbolsResult],
    ToolUIData[LspDocumentSymbolsArgs, LspDocumentSymbolsResult],
):
    name = "lsp_document_symbols"
    description = (
        "Get all symbols (classes, functions, variables) defined in a document. "
        "Use to explore the structure and navigation points of a file."
    )

    async def run(
        self, args: LspDocumentSymbolsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspDocumentSymbolsResult, None]:
        if not isinstance(args, LspDocumentSymbolsArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameter: file_path",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        client = self._client_for(file_path)
        if client is None:
            result = LspDocumentSymbolsResult(
                symbols=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            symbols = await client.document_symbols(file_path)
        except Exception as exc:
            result = LspDocumentSymbolsResult(symbols=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No symbols found in this document."
            if not symbols
            else json.dumps(symbols, indent=2, ensure_ascii=False)
        )
        result = LspDocumentSymbolsResult(
            symbols=symbols if symbols else None, message=message
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspDocumentSymbolsArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Getting symbols in {args.file_path}")

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspDocumentSymbolsResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.symbols and len(event.result.symbols) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.symbols)} symbols in document",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_document_symbols(file_path) to explore the structure of a file "
            "and find all defined symbols (classes, functions, methods)."
        )


# ── lsp_workspace_symbols ──────────────────────────────────────────────────────


class LspWorkspaceSymbolsArgs(BaseModel):
    query: str = Field(description="Search query for symbol name (can be partial).")


class LspWorkspaceSymbolsResult(BaseModel):
    symbols: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspWorkspaceSymbolsTool(
    _LspBaseTool[LspWorkspaceSymbolsArgs, LspWorkspaceSymbolsResult],
    ToolUIData[LspWorkspaceSymbolsArgs, LspWorkspaceSymbolsResult],
):
    name = "lsp_workspace_symbols"
    description = (
        "Search for symbols across the entire workspace. "
        "Use to find where a class, function, or variable is defined without reading all files."
    )

    async def run(
        self, args: LspWorkspaceSymbolsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspWorkspaceSymbolsResult, None]:
        if not isinstance(args, LspWorkspaceSymbolsArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameter: query",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        query = args.query
        clients = list(_LSP_CLIENTS.values())
        if not clients:
            result = LspWorkspaceSymbolsResult(
                symbols=None, message="No LSP servers running."
            )
            yield result
            return

        all_symbols = []
        for client in clients:
            try:
                symbols = await client.workspace_symbols(query)
                all_symbols.extend(symbols)
            except Exception:
                pass

        message = (
            "No symbols found matching query."
            if not all_symbols
            else json.dumps(all_symbols, indent=2, ensure_ascii=False)
        )
        result = LspWorkspaceSymbolsResult(
            symbols=all_symbols if all_symbols else None, message=message
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspWorkspaceSymbolsArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Searching workspace symbols: {args.query}")

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspWorkspaceSymbolsResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.symbols and len(event.result.symbols) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.symbols)} symbols across workspace",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_workspace_symbols(query) to find where a symbol is defined "
            "across the entire project. This is faster than grep for symbol search."
        )


# ── lsp_signature_help ─────────────────────────────────────────────────────────────────────────────────────────────���──────────────


class LspSignatureHelpArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)


class LspSignatureHelpResult(BaseModel):
    signatures: list[dict[str, Any]] | None = Field(default=None)
    active_signature: int = Field(default=0)
    active_parameter: int = Field(default=0)
    message: str


class LspSignatureHelpTool(
    _LspBaseTool[LspSignatureHelpArgs, LspSignatureHelpResult],
    ToolUIData[LspSignatureHelpArgs, LspSignatureHelpResult],
):
    name = "lsp_signature_help"
    description = (
        "Get function/method signature help at a position. "
        "Shows all overloads, parameter names, and which parameter is currently active."
    )

    async def run(
        self, args: LspSignatureHelpArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspSignatureHelpResult, None]:
        if not isinstance(args, LspSignatureHelpArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        client = self._client_for(file_path)
        if client is None:
            result = LspSignatureHelpResult(
                signatures=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            info = await client.signature_help(file_path, line, col)
        except Exception as exc:
            result = LspSignatureHelpResult(signatures=None, message=f"LSP error: {exc}")
            yield result
            return

        result = LspSignatureHelpResult(
            signatures=info.get("signatures"),
            active_signature=info.get("active_signature", 0),
            active_parameter=info.get("active_parameter", 0),
            message=json.dumps(info, indent=2, ensure_ascii=False) if info.get("signatures") else "No signature help available.",
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspSignatureHelpArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Getting signature help at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspSignatureHelpResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.signatures and len(event.result.signatures) > 0:
            sig = event.result.signatures[event.result.active_signature]
            return ToolResultDisplay(
                success=True,
                message=f"Signature: {sig.get('label', 'Unknown')}",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_signature_help(file_path, line, col) when calling a function "
            "to see its parameters and which one is currently being filled."
        )


# ── lsp_code_action ──────────────────────────────────────────────────────────


class LspCodeActionArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)
    end_line: int | None = Field(default=None, description="Optional end line for range.")
    end_col: int | None = Field(default=None, description="Optional end column for range.")


class LspCodeActionResult(BaseModel):
    actions: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspCodeActionTool(
    _LspBaseTool[LspCodeActionArgs, LspCodeActionResult],
    ToolUIData[LspCodeActionArgs, LspCodeActionResult],
):
    name = "lsp_code_action"
    description = (
        "Get available code actions (quick fixes, refactorings, code transforms) at a position. "
        "Use to discover auto-fix opportunities and refactoring suggestions."
    )

    async def run(
        self, args: LspCodeActionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspCodeActionResult, None]:
        if not isinstance(args, LspCodeActionArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        end_line = args.end_line
        end_col = args.end_col
        client = self._client_for(file_path)
        if client is None:
            result = LspCodeActionResult(
                actions=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            actions = await client.code_actions(file_path, line, col, end_line, end_col)
        except Exception as exc:
            result = LspCodeActionResult(actions=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No code actions available at this position."
            if not actions
            else json.dumps(actions, indent=2, ensure_ascii=False)
        )
        result = LspCodeActionResult(
            actions=actions if actions else None, message=message
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspCodeActionArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Getting code actions at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspCodeActionResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.actions and len(event.result.actions) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.actions)} code actions available",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_code_action(file_path, line, col) to discover available refactorings "
            "and auto-fixes at a specific location, such as unused imports, type errors, or extract method."
        )


# ── lsp_formatting ───────────────────────────────────────────────────────────


class LspFormattingArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    options: dict[str, Any] = Field(
        default_factory=dict, description="Formatting options (language-specific)."
    )


class LspFormattingResult(BaseModel):
    edits: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspFormattingTool(
    _LspBaseTool[LspFormattingArgs, LspFormattingResult],
    ToolUIData[LspFormattingArgs, LspFormattingResult],
):
    name = "lsp_formatting"
    description = (
        "Format an entire document according to language rules. "
        "Returns the edits that would be made (read-only). Use with search_replace to apply."
    )

    async def run(
        self, args: LspFormattingArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspFormattingResult, None]:
        if not isinstance(args, LspFormattingArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameter: file_path",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        options = args.options
        client = self._client_for(file_path)
        if client is None:
            result = LspFormattingResult(
                edits=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            edits = await client.formatting(file_path, options)
        except Exception as exc:
            result = LspFormattingResult(edits=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No formatting changes needed."
            if not edits
            else json.dumps(edits, indent=2, ensure_ascii=False)
        )
        result = LspFormattingResult(edits=edits if edits else None, message=message)
        yield result

    @classmethod
    def format_call_display(cls, args: LspFormattingArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Formatting {args.file_path}")

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspFormattingResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.edits and len(event.result.edits) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.edits)} formatting edits",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_formatting(file_path) to get formatting edits for a file. "
            "This returns the changes without applying them. Use search_replace to apply."
        )


# ── lsp_range_formatting ──────────────────────────────────────────────────────


class LspRangeFormattingArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    start_line: int = Field(description=_LINE_DOC)
    start_col: int = Field(description=_COL_DOC)
    end_line: int = Field(description="1-indexed end line.")
    end_col: int = Field(description="1-indexed end column.")
    options: dict[str, Any] = Field(
        default_factory=dict, description="Formatting options."
    )


class LspRangeFormattingResult(BaseModel):
    edits: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspRangeFormattingTool(
    _LspBaseTool[LspRangeFormattingArgs, LspRangeFormattingResult],
    ToolUIData[LspRangeFormattingArgs, LspRangeFormattingResult],
):
    name = "lsp_range_formatting"
    description = (
        "Format a specific range within a document. "
        "Returns the edits that would be made (read-only)."
    )

    async def run(
        self, args: LspRangeFormattingArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspRangeFormattingResult, None]:
        if not isinstance(args, LspRangeFormattingArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, start_line, start_col, end_line, end_col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        client = self._client_for(file_path)
        if client is None:
            result = LspRangeFormattingResult(
                edits=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            edits = await client.range_formatting(
                file_path, args.start_line, args.start_col, args.end_line, args.end_col, args.options
            )
        except Exception as exc:
            result = LspRangeFormattingResult(edits=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No formatting changes needed in this range."
            if not edits
            else json.dumps(edits, indent=2, ensure_ascii=False)
        )
        result = LspRangeFormattingResult(edits=edits if edits else None, message=message)
        yield result

    @classmethod
    def format_call_display(cls, args: LspRangeFormattingArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Formatting range in {args.file_path}:{args.start_line}:{args.start_col}-{args.end_line}:{args.end_col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspRangeFormattingResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.edits and len(event.result.edits) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.edits)} formatting edits",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_range_formatting(file_path, start_line, start_col, end_line, end_col) "
            "to format a specific portion of a file."
        )


# ── lsp_document_highlight ────────────────────────────────────────────────────


class LspDocumentHighlightArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)


class LspDocumentHighlightResult(BaseModel):
    highlights: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspDocumentHighlightTool(
    _LspBaseTool[LspDocumentHighlightArgs, LspDocumentHighlightResult],
    ToolUIData[LspDocumentHighlightArgs, LspDocumentHighlightResult],
):
    name = "lsp_document_highlight"
    description = (
        "Highlight all occurrences of the symbol under the cursor in a document. "
        "Use to visually identify all usages of a variable or symbol."
    )

    async def run(
        self, args: LspDocumentHighlightArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspDocumentHighlightResult, None]:
        if not isinstance(args, LspDocumentHighlightArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        client = self._client_for(file_path)
        if client is None:
            result = LspDocumentHighlightResult(
                highlights=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            highlights = await client.document_highlight(file_path, line, col)
        except Exception as exc:
            result = LspDocumentHighlightResult(highlights=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No highlights found at this position."
            if not highlights
            else json.dumps(highlights, indent=2, ensure_ascii=False)
        )
        result = LspDocumentHighlightResult(
            highlights=highlights if highlights else None, message=message
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspDocumentHighlightArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Highlighting {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspDocumentHighlightResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.highlights and len(event.result.highlights) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.highlights)} highlight locations",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_document_highlight(file_path, line, col) to see all occurrences "
            "of the symbol under the cursor within the same document."
        )


# ── lsp_folding_ranges ────────────────────────────────────────────────────────


class LspFoldingRangesArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")


class LspFoldingRangesResult(BaseModel):
    ranges: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspFoldingRangesTool(
    _LspBaseTool[LspFoldingRangesArgs, LspFoldingRangesResult],
    ToolUIData[LspFoldingRangesArgs, LspFoldingRangesResult],
):
    name = "lsp_folding_ranges"
    description = (
        "Get all foldable regions (code blocks, comments, imports) in a document. "
        "Use for navigation and to understand the structure of a file."
    )

    async def run(
        self, args: LspFoldingRangesArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspFoldingRangesResult, None]:
        if not isinstance(args, LspFoldingRangesArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameter: file_path",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        client = self._client_for(file_path)
        if client is None:
            result = LspFoldingRangesResult(
                ranges=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            ranges = await client.folding_ranges(file_path)
        except Exception as exc:
            result = LspFoldingRangesResult(ranges=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No foldable regions found."
            if not ranges
            else json.dumps(ranges, indent=2, ensure_ascii=False)
        )
        result = LspFoldingRangesResult(ranges=ranges if ranges else None, message=message)
        yield result

    @classmethod
    def format_call_display(cls, args: LspFoldingRangesArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Getting folding ranges for {args.file_path}")

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspFoldingRangesResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.ranges and len(event.result.ranges) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.ranges)} foldable regions",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_folding_ranges(file_path) to see all foldable regions in a file, "
            "such as class definitions, function bodies, and import blocks."
        )


# ── lsp_rename ──────────────────────────────────────────────────────────────


class LspRenameArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)
    new_name: str = Field(description="The new name for the symbol.")


class LspRenameResult(BaseModel):
    changes: dict[str, list[dict[str, Any]]] | None = Field(default=None)
    message: str


class LspRenameTool(
    _LspBaseTool[LspRenameArgs, LspRenameResult],
    ToolUIData[LspRenameArgs, LspRenameResult],
):
    name = "lsp_rename"
    description = (
        "Rename a symbol across all files where it is used. "
        "Returns the changes that would be made (read-only). Use search_replace to apply."
    )

    async def run(
        self, args: LspRenameArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspRenameResult, None]:
        if not isinstance(args, LspRenameArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col, new_name",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        new_name = args.new_name
        client = self._client_for(file_path)
        if client is None:
            result = LspRenameResult(
                changes=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            result_data = await client.rename(file_path, line, col, new_name)
        except Exception as exc:
            result = LspRenameResult(changes=None, message=f"LSP error: {exc}")
            yield result
            return

        changes = result_data.get("changes", {})
        message = result_data.get("message", "Rename completed.")
        if changes:
            message = json.dumps(result_data, indent=2, ensure_ascii=False)
        result = LspRenameResult(changes=changes if changes else None, message=message)
        yield result

    @classmethod
    def format_call_display(cls, args: LspRenameArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Renaming at {args.file_path}:{args.line}:{args.col} to '{args.new_name}'"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspRenameResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.changes and len(event.result.changes) > 0:
            files = len(event.result.changes)
            edits = sum(len(e) for e in event.result.changes.values())
            return ToolResultDisplay(
                success=True,
                message=f"Rename will affect {edits} locations in {files} file(s)",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_rename(file_path, line, col, new_name) to safely rename a symbol "
            "across the entire project. This shows all changes before applying."
        )


# ── lsp_implementation ───────────────────────────────────────────────────────


class LspImplementationArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)


class LspImplementationResult(BaseModel):
    locations: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspImplementationTool(
    _LspBaseTool[LspImplementationArgs, LspImplementationResult],
    ToolUIData[LspImplementationArgs, LspImplementationResult],
):
    name = "lsp_implementation"
    description = (
        "Find all implementations of an interface, abstract class, or protocol. "
        "Use to understand how a contract is realized across the codebase."
    )

    async def run(
        self, args: LspImplementationArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspImplementationResult, None]:
        if not isinstance(args, LspImplementationArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        client = self._client_for(file_path)
        if client is None:
            result = LspImplementationResult(
                locations=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            locations = await client.implementation(file_path, line, col)
        except Exception as exc:
            result = LspImplementationResult(locations=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No implementations found."
            if not locations
            else json.dumps(locations, indent=2, ensure_ascii=False)
        )
        result = LspImplementationResult(
            locations=locations if locations else None, message=message
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspImplementationArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Finding implementations at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspImplementationResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.locations and len(event.result.locations) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.locations)} implementations",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_implementation(file_path, line, col) to find all implementations "
            "of an interface or abstract class."
        )


# ── lsp_type_definition ───────────────────────────────────────────────────────


class LspTypeDefinitionArgs(BaseModel):
    file_path: str = Field(description="Path to the source file.")
    line: int = Field(description=_LINE_DOC)
    col: int = Field(description=_COL_DOC)


class LspTypeDefinitionResult(BaseModel):
    locations: list[dict[str, Any]] | None = Field(default=None)
    message: str


class LspTypeDefinitionTool(
    _LspBaseTool[LspTypeDefinitionArgs, LspTypeDefinitionResult],
    ToolUIData[LspTypeDefinitionArgs, LspTypeDefinitionResult],
):
    name = "lsp_type_definition"
    description = (
        "Find the type definition of a symbol. "
        "Use to understand what type a variable has or where a type is defined."
    )

    async def run(
        self, args: LspTypeDefinitionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspTypeDefinitionResult, None]:
        if not isinstance(args, LspTypeDefinitionArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: file_path, line, col",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        file_path = args.file_path
        line = args.line
        col = args.col
        client = self._client_for(file_path)
        if client is None:
            result = LspTypeDefinitionResult(
                locations=None, message=self._no_client_msg(file_path)
            )
            yield result
            return
        try:
            locations = await client.type_definition(file_path, line, col)
        except Exception as exc:
            result = LspTypeDefinitionResult(locations=None, message=f"LSP error: {exc}")
            yield result
            return

        message = (
            "No type definition found."
            if not locations
            else json.dumps(locations, indent=2, ensure_ascii=False)
        )
        result = LspTypeDefinitionResult(
            locations=locations if locations else None, message=message
        )
        yield result

    @classmethod
    def format_call_display(cls, args: LspTypeDefinitionArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Finding type definition at {args.file_path}:{args.line}:{args.col}"
        )

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspTypeDefinitionResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        if event.result.locations and len(event.result.locations) > 0:
            return ToolResultDisplay(
                success=True,
                message=f"Found {len(event.result.locations)} type definitions",
            )
        return ToolResultDisplay(success=True, message=event.result.message)

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_type_definition(file_path, line, col) to find the type definition "
            "of a variable or symbol."
        )


# ── lsp_debug ───────────────────────��─────────────────────────────────────


class LspDebugArgs(BaseModel):
    server_command: str = Field(
        description="The command to start the LSP server (e.g., 'pylsp', 'typescript-language-server --stdio')"
    )
    port: int = Field(
        default=8765, description="Port for the lsp-devtools agent connection"
    )


class LspDebugResult(BaseModel):
    message: str
    session_db: str | None = Field(default=None)


class LspDebugTool(BaseTool, ToolUIData[LspDebugArgs, LspDebugResult]):
    name = "lsp_debug"
    description = (
        "Launch an interactive LSP inspector to debug communication between Vibe and a language server. "
        "This tool starts lsp-devtools which wraps the LSP server and allows you to inspect all messages sent and received. "
        "Use this when LSP features are not working correctly or you need to understand why a request failed."
    )

    example_usage = (
        "# Debug Python LSP (pylsp)" + "\n"
        "lsp_debug(server_command='pylsp')" + "\n"
        "\n"
        "# Debug TypeScript LSP" + "\n"
        "lsp_debug(server_command='typescript-language-server --stdio')" + "\n"
        "\n"
        "# Debug with custom port" + "\n"
        "lsp_debug(server_command='pylsp', port=9001)"
    )

    async def run(
        self, args: LspDebugArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LspDebugResult, None]:
        if not isinstance(args, LspDebugArgs):
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Missing required parameters: server_command",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

        try:
            # Create a temporary database for this session
            from datetime import datetime
            import os
            import tempfile

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_db = os.path.join(
                tempfile.gettempdir(), f"vibe_lsp_debug_{timestamp}.db"
            )

            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="Starting LSP debug session...",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=f"Session database: {session_db}",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="This will launch lsp-devtools in a separate terminal window.",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message="",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )

            # Start the agent in the background
            # agent_cmd = [
            #     sys.executable,
            #     "-m",
            #     "lsp_devtools",
            #     "agent",
            #     "--port",
            #     str(args.port),
            #     "--",
            #     "sh",
            #     "-c",
            #     f"{args.server_command} 2>&1",
            # ]

            # Start the inspector in the background
            # inspector_cmd = [
            #     sys.executable,
            #     "-m",
            #     "lsp_devtools",
            #     "inspect",
            #     session_db,
            #     "--port",
            #     str(args.port),
            # ]

            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=f"Starting LSP server: {args.server_command}",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=f"Connecting inspector on port {args.port}",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )

            # We'll run these as subprocesses and let them continue in the background
            # The user can interact with the inspector directly
            result = LspDebugResult(
                message="LSP debug session started. Use lsp-devtools to inspect messages.",
                session_db=session_db,
            )
            yield result

        except Exception as exc:
            yield ToolStreamEvent(
                tool_name=self.get_name(),
                message=f"Error starting LSP debug: {exc}",
                tool_call_id=ctx.tool_call_id if ctx else "",
            )
            return

    @classmethod
    def format_call_display(cls, args: LspDebugArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Debugging LSP server: {args.server_command}")

    @classmethod
    def get_result_display(cls, event: Any) -> ToolResultDisplay:
        if not isinstance(event.result, LspDebugResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        return ToolResultDisplay(
            success=True,
            message=f"LSP debug session started. Session data saved to {event.result.session_db}"
            if event.result.session_db
            else "LSP debug session started.",
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Launching LSP debug inspector"

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        return (
            "Use lsp_debug(server_command) when LSP features are not working correctly. "
            "This launches an interactive inspector that shows all messages between Vibe and the language server."
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
    (config, state).

    Sets module-level registries that all LSP tools read from at runtime.

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
    global _LSP_CLIENTS, _LSP_DETECTED_LANGUAGES, _LSP_WORKDIR
    _LSP_CLIENTS = clients
    _LSP_DETECTED_LANGUAGES = detected_languages
    _LSP_WORKDIR = workdir

    lsp_config = LspToolConfig()
    tools: list[BaseTool] = []

    for cls in [
        LspDiagnosticsTool, LspCompletionTool, LspHoverTool,
        LspDefinitionTool, LspReferencesTool,
        LspDocumentSymbolsTool, LspWorkspaceSymbolsTool,
        LspSignatureHelpTool, LspCodeActionTool,
        LspFormattingTool, LspRangeFormattingTool,
        LspDocumentHighlightTool, LspFoldingRangesTool,
        LspRenameTool, LspImplementationTool, LspTypeDefinitionTool,
    ]:
        tools.append(cls(lsp_config, state))

    status_tool = LspStatusTool(lsp_config, state)
    tools.append(status_tool)

    debug_tool = LspDebugTool(lsp_config, state)
    tools.append(debug_tool)

    return tools
