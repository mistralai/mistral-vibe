"""vibe/core/plugins/builtin/lsp/lsp_client.py
────────────────────────────────────────────────────────────────────────────
Async wrapper around a single Language Server Protocol process.

Uses lsp-client library (supports lsprotocol 2025+).
Each LspClient instance owns one LSP subprocess for one language.

Lifecycle
────────
    client = LspClient(cfg, root)
    await client.start()          # spawn process
    ...
    diags  = await client.diagnostics("src/foo.py")
    ...
    await client.stop()        # shutdown

Document synchronisation
───────────────────────
Files are opened on-demand via request methods.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from vibe.core.plugins.builtin.lsp.registry import LspConfig

logger = logging.getLogger(__name__)

# Seconds to wait for server to respond to requests.
_REQUEST_TIMEOUT = 10.0

# Map language to lsp-client client class
_LSP_CLIENTS: dict[str, str] = {
    "python": "BasedpyrightClient",
    "typescript": "TypescriptClient",
    "rust": "RustAnalyzerClient",
    "go": "GoplsClient",
    "deno": "DenoClient",
}


class LspClientError(RuntimeError):
    """Raised when an LSP operation fails."""


class LspClient:
    """Manages one LSP server subprocess for a single language.

    Parameters
    ----------
    config:
        :class:`~vibe.core.plugins.builtin.lsp.registry.LspConfig` that
        describes the server to start.
    root:
        Absolute path to the project root (workdir).
    """

    def __init__(self, config: LspConfig, root: Path) -> None:
        self._cfg = config
        self._root = root
        self._client: Any = None
        self._started = False
        self._client_class: type | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn the LSP process and perform the LSP handshake."""
        if self._started:
            return
        if not self._cfg.is_available():
            raise LspClientError(
                f"LSP executable not found: {self._cfg.command[0]!r}. "
                f"Please install the {self._cfg.language} language server."
            )

        try:
            from lsp_client import clients
        except ImportError as exc:
            raise LspClientError(f"lsp-client not installed: {exc}") from exc

        client_class_name = _LSP_CLIENTS.get(self._cfg.language)
        if not client_class_name:
            raise LspClientError(
                f"No lsp-client for language: {self._cfg.language}. "
                f"Supported: {list(_LSP_CLIENTS.keys())}"
            )

        try:
            self._client_class = getattr(clients, client_class_name)
        except AttributeError as exc:
            raise LspClientError(
                f"Client class not found: {client_class_name}"
            ) from exc

        try:
            # Use async context manager - creates and starts the client
            self._client = self._client_class(
                server="local",
                workspace=self._root,
                sync_file=True,
                request_timeout=_REQUEST_TIMEOUT,
            )
            # Start server (async context manager enters and starts)
            await self._client.__aenter__()
        except Exception as exc:
            logger.error("Failed to start LSP for %s: %s", self._cfg.language, exc)
            self._client = None
            raise LspClientError(
                f"Failed to start LSP for {self._cfg.language!r}: {exc}"
            ) from exc

        self._started = True
        logger.info("LSP started: %s (%s)", self._cfg.language, self._cfg.command[0])

    async def stop(self) -> None:
        """Send shutdown/exit to the server and clean up."""
        if not self._started or self._client is None:
            return
        try:
            await self._client.__aexit__(None, None, None)
        except Exception as exc:
            logger.debug("LSP stop error (%s): %s", self._cfg.language, exc)
        finally:
            self._started = False
            self._client = None

    # ── LSP capabilities ────────────────────────────────────────────────────────

    async def diagnostics(self, file_path: str) -> list[dict[str, Any]]:
        """Return diagnostics for *file_path*."""
        self._assert_started()

        try:
            result = await self._client.request_text_document_diagnostics(
                file_path=file_path,
            )
        except Exception as e:
            logger.warning("diagnostics error: %s", e)
            return []

        if not result:
            return []

        return [
            self._format_diagnostic(d) for d in result
        ]

    async def completion(
        self, file_path: str, line: int, col: int
    ) -> list[dict[str, Any]]:
        """Return completion items at the given 1-indexed position."""
        self._assert_started()
        from lsp_client import Position

        try:
            result = await self._client.request_completion(
                file_path=file_path,
                position=Position(line - 1, col - 1),
            )
        except Exception as e:
            logger.warning("completion error: %s", e)
            return []

        items = result if isinstance(result, list) else []
        return [self._format_completion_item(i) for i in items[:50]]

    async def hover(self, file_path: str, line: int, col: int) -> dict[str, Any]:
        """Return hover information at the given 1-indexed position."""
        self._assert_started()
        from lsp_client import Position

        try:
            result = await self._client.request_hover(
                file_path=file_path,
                position=Position(line - 1, col - 1),
            )
        except Exception as e:
            logger.warning("hover error: %s", e)
            return {"content": "", "kind": "plaintext"}

        if not result:
            return {"content": "", "kind": "plaintext"}

        # Result can be string or MarkupContent
        if hasattr(result, "contents"):
            contents = result.contents
            if hasattr(contents, "value"):
                return {"content": contents.value, "kind": getattr(contents, "kind", "markdown")}
            return {"content": str(contents), "kind": "plaintext"}
        return {"content": str(result), "kind": "plaintext"}

    async def definition(
        self, file_path: str, line: int, col: int
    ) -> list[dict[str, Any]]:
        """Return the definition location(s) of the symbol at position."""
        self._assert_started()
        from lsp_client import Position

        try:
            result = await self._client.request_definition_locations(
                file_path=file_path,
                position=Position(line - 1, col - 1),
            )
        except Exception as e:
            logger.warning("definition error: %s", e)
            return []

        locations = result if isinstance(result, list) else ([result] if result else [])
        return [self._format_location(loc) for loc in locations if loc is not None]

    async def references(
        self, file_path: str, line: int, col: int, include_declaration: bool = True
    ) -> list[dict[str, Any]]:
        """Return all references to the symbol at position."""
        self._assert_started()
        from lsp_client import Position

        try:
            result = await self._client.request_references(
                file_path=file_path,
                position=Position(line - 1, col - 1),
                include_declaration=include_declaration,
            )
        except Exception as e:
            logger.warning("references error: %s", e)
            return []

        return [self._format_location(loc) for loc in (result or [])]

    async def document_symbols(self, file_path: str) -> list[dict[str, Any]]:
        """Return all symbols defined in a document."""
        self._assert_started()
        try:
            result = await self._client.request_document_symbol(file_path=file_path)
        except Exception as e:
            logger.warning("document_symbols error: %s", e)
            return []
        symbols = result if isinstance(result, list) else ([result] if result else [])
        return [self._format_document_symbol(s) for s in symbols]

    async def workspace_symbols(self, query: str) -> list[dict[str, Any]]:
        """Search symbols across the workspace."""
        self._assert_started()
        try:
            result = await self._client.request_workspace_symbol(query=query)
        except Exception as e:
            logger.warning("workspace_symbols error: %s", e)
            return []
        return [self._format_workspace_symbol(s) for s in (result or [])]

    async def rename(
        self, file_path: str, line: int, col: int, new_name: str
    ) -> dict[str, Any]:
        """Rename a symbol across all references."""
        self._assert_started()
        from lsp_client import Position
        try:
            result = await self._client.request_rename(
                file_path=file_path,
                position=Position(line - 1, col - 1),
                new_name=new_name,
            )
        except Exception as e:
            logger.warning("rename error: %s", e)
            return {}
        return self._format_rename_result(result)

    async def formatting(
        self, file_path: str, options: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Format a document according to language rules."""
        self._assert_started()
        try:
            result = await self._client.request_document_formatting(
                file_path=file_path,
                options=options or {},
            )
        except Exception as e:
            logger.warning("formatting error: %s", e)
            return []
        return [self._format_text_edit(e) for e in (result or [])]

    async def range_formatting(
        self,
        file_path: str,
        start_line: int,
        start_col: int,
        end_line: int,
        end_col: int,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Format a specific range in a document."""
        self._assert_started()
        from lsp_client import Position, Range
        try:
            result = await self._client.request_document_range_formatting(
                file_path=file_path,
                range=Range(
                    Position(start_line - 1, start_col - 1),
                    Position(end_line - 1, end_col - 1),
                ),
                options=options or {},
            )
        except Exception as e:
            logger.warning("range_formatting error: %s", e)
            return []
        return [self._format_text_edit(e) for e in (result or [])]

    async def code_actions(
        self,
        file_path: str,
        line: int,
        col: int,
        end_line: int | None = None,
        end_col: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get available code actions at a position."""
        self._assert_started()
        from lsp_client import Position, Range
        try:
            start_pos = Position(line - 1, col - 1)
            end_pos = Position((end_line or line) - 1, (end_col or col) - 1)
            result = await self._client.request_code_actions(
                file_path=file_path,
                range=Range(start_pos, end_pos),
            )
        except Exception as e:
            logger.warning("code_actions error: %s", e)
            return []
        return [self._format_code_action(a) for a in (result or [])]

    async def signature_help(
        self, file_path: str, line: int, col: int
    ) -> dict[str, Any]:
        """Get function signature help at a position."""
        self._assert_started()
        from lsp_client import Position
        try:
            result = await self._client.request_signature_help(
                file_path=file_path,
                position=Position(line - 1, col - 1),
            )
        except Exception as e:
            logger.warning("signature_help error: %s", e)
            return {}
        return self._format_signature_help(result)

    async def document_highlight(
        self, file_path: str, line: int, col: int
    ) -> list[dict[str, Any]]:
        """Highlight all references to symbol under cursor."""
        self._assert_started()
        from lsp_client import Position
        try:
            result = await self._client.request_document_highlight(
                file_path=file_path,
                position=Position(line - 1, col - 1),
            )
        except Exception as e:
            logger.warning("document_highlight error: %s", e)
            return []
        return [self._format_document_highlight(h) for h in (result or [])]

    async def folding_ranges(self, file_path: str) -> list[dict[str, Any]]:
        """Get foldable code regions in a document."""
        self._assert_started()
        try:
            result = await self._client.request_folding_ranges(file_path=file_path)
        except Exception as e:
            logger.warning("folding_ranges error: %s", e)
            return []
        return [self._format_folding_range(r) for r in (result or [])]

    async def implementation(
        self, file_path: str, line: int, col: int
    ) -> list[dict[str, Any]]:
        """Find implementations of an interface or abstract definition."""
        self._assert_started()
        from lsp_client import Position
        try:
            result = await self._client.request_implementation(
                file_path=file_path,
                position=Position(line - 1, col - 1),
            )
        except Exception as e:
            logger.warning("implementation error: %s", e)
            return []
        locations = result if isinstance(result, list) else ([result] if result else [])
        return [self._format_location(loc) for loc in locations]

    async def type_definition(
        self, file_path: str, line: int, col: int
    ) -> list[dict[str, Any]]:
        """Find type definition of a symbol."""
        self._assert_started()
        from lsp_client import Position
        try:
            result = await self._client.request_type_definition(
                file_path=file_path,
                position=Position(line - 1, col - 1),
            )
        except Exception as e:
            logger.warning("type_definition error: %s", e)
            return []
        locations = result if isinstance(result, list) else ([result] if result else [])
        return [self._format_location(loc) for loc in locations]

    # ── Formatters ────────────────────────────────────────────────────────────

    @staticmethod
    def _format_diagnostic(d: Any) -> dict[str, Any]:
        severity_map = {1: "Error", 2: "Warning", 3: "Information", 4: "Hint"}
        
        # Handle different response formats
        if hasattr(d, "severity"):
            sev = d.severity.value if hasattr(d.severity, "value") else d.severity
        elif isinstance(d, dict):
            sev = d.get("severity")
        else:
            sev = None
        
        if hasattr(d, "range"):
            rng = d.range
            line = rng.start.line + 1 if hasattr(rng.start, "line") else 1
            col = rng.start.character + 1 if hasattr(rng.start, "character") else 1
            end_line = rng.end.line + 1 if hasattr(rng.end, "line") else line
            end_col = rng.end.character + 1 if hasattr(rng.end, "character") else col
        elif isinstance(d, dict):
            rng = d.get("range", {})
            line = rng.get("start", {}).get("line", 0) + 1
            col = rng.get("start", {}).get("character", 0) + 1
            end_line = rng.get("end", {}).get("line", 0) + 1
            end_col = rng.get("end", {}).get("character", 0) + 1
        else:
            line = col = end_line = end_col = 1

        return {
            "severity": severity_map.get(int(sev) if sev else 0, "Unknown"),
            "line": line,
            "col": col,
            "end_line": end_line,
            "end_col": end_col,
            "message": getattr(d, "message", d.get("message", "") if isinstance(d, dict) else ""),
            "source": getattr(d, "source", d.get("source", "") if isinstance(d, dict) else ""),
            "code": str(getattr(d, "code", d.get("code", "") if isinstance(d, dict) else "")),
        }

    @staticmethod
    def _format_completion_item(i: Any) -> dict[str, Any]:
        kind_map = {
            1: "Text", 2: "Method", 3: "Function", 4: "Constructor",
            5: "Field", 6: "Variable", 7: "Class", 8: "Interface",
            9: "Module", 10: "Property", 14: "Keyword", 17: "File",
        }
        
        kind = getattr(i, "kind", None) if hasattr(i, "kind") else i.get("kind") if isinstance(i, dict) else None
        if hasattr(kind, "value"):
            kind = kind.value
        
        return {
            "label": getattr(i, "label", i.get("label", "") if isinstance(i, dict) else ""),
            "kind": kind_map.get(int(kind) if kind else 0, "Unknown"),
            "detail": getattr(i, "detail", i.get("detail", "") if isinstance(i, dict) else ""),
            "documentation": getattr(i, "documentation", "") or (i.get("documentation", {}).get("value", "") if isinstance(i, dict) else ""),
        }

    @staticmethod
    def _format_location(loc: Any) -> dict[str, Any]:
        uri = getattr(loc, "uri", "") or (loc.get("uri") if isinstance(loc, dict) else "")
        file_path = uri.replace("file://", "") if uri.startswith("file://") else uri
        
        if hasattr(loc, "range"):
            rng = loc.range
            line = rng.start.line + 1 if hasattr(rng.start, "line") else 1
            col = rng.start.character + 1 if hasattr(rng.start, "character") else 1
        elif isinstance(loc, dict):
            rng = loc.get("range", {})
            line = rng.get("start", {}).get("line", 0) + 1
            col = rng.get("start", {}).get("character", 0) + 1
        else:
            line = col = 1

        return {"file": file_path, "line": line, "col": col}

    @staticmethod
    def _format_range(rng: Any) -> dict[str, Any]:
        if hasattr(rng, "start"):
            start_line = rng.start.line + 1 if hasattr(rng.start, "line") else 1
            start_col = rng.start.character + 1 if hasattr(rng.start, "character") else 1
            end_line = rng.end.line + 1 if hasattr(rng.end, "line") else start_line
            end_col = rng.end.character + 1 if hasattr(rng.end, "character") else start_col
        elif isinstance(rng, dict):
            start_line = rng.get("start", {}).get("line", 0) + 1
            start_col = rng.get("start", {}).get("character", 0) + 1
            end_line = rng.get("end", {}).get("line", 0) + 1
            end_col = rng.get("end", {}).get("character", 0) + 1
        else:
            start_line = start_col = end_line = end_col = 1
        return {"start_line": start_line, "start_col": start_col, "end_line": end_line, "end_col": end_col}

    @staticmethod
    def _format_document_symbol(s: Any) -> dict[str, Any]:
        name = getattr(s, "name", s.get("name", "") if isinstance(s, dict) else "")
        kind = getattr(s, "kind", None) if hasattr(s, "kind") else s.get("kind") if isinstance(s, dict) else None
        if hasattr(kind, "value"):
            kind = kind.value
        kind_map = {1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
                    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
                    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
                    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
                    20: "Key", 21: "Null", 22: "EnumMember", 23: "Event", 24: "Operator",
                    25: "TypeParameter"}
        detail = getattr(s, "detail", s.get("detail", "") if isinstance(s, dict) else "")
        rng = getattr(s, "range", None) or getattr(s, "selectionRange", None) or (s.get("range") if isinstance(s, dict) else None)
        location = LspClient._format_range(rng) if rng else {"start_line": 1, "start_col": 1, "end_line": 1, "end_col": 1}
        return {
            "name": name,
            "kind": kind_map.get(int(kind) if kind else 0, "Unknown"),
            "detail": detail,
            "file": location.get("file", ""),
            "line": location.get("start_line", 1),
            "col": location.get("start_col", 1),
        }

    @staticmethod
    def _format_workspace_symbol(s: Any) -> dict[str, Any]:
        name = getattr(s, "name", s.get("name", "") if isinstance(s, dict) else "")
        kind = getattr(s, "kind", None) if hasattr(s, "kind") else s.get("kind") if isinstance(s, dict) else None
        if hasattr(kind, "value"):
            kind = kind.value
        kind_map = {1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
                    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
                    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
                    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
                    20: "Key", 21: "Null", 22: "EnumMember", 23: "Event", 24: "Operator",
                    25: "TypeParameter"}
        loc = getattr(s, "location", s.get("location") if isinstance(s, dict) else None)
        if loc:
            file_path = getattr(loc, "uri", "") or (loc.get("uri") if isinstance(loc, dict) else "")
            file_path = file_path.replace("file://", "") if file_path.startswith("file://") else file_path
            rng = getattr(loc, "range", None) or (loc.get("range") if isinstance(loc, dict) else None)
            line = rng.start.line + 1 if rng and hasattr(rng.start, "line") else 1
            col = rng.start.character + 1 if rng and hasattr(rng.start, "character") else 1
        else:
            file_path = ""
            line = col = 1
        return {"name": name, "kind": kind_map.get(int(kind) if kind else 0, "Unknown"), "file": file_path, "line": line, "col": col}

    @staticmethod
    def _format_text_edit(e: Any) -> dict[str, Any]:
        rng = getattr(e, "range", None) or (e.get("range") if isinstance(e, dict) else None)
        range_dict = LspClient._format_range(rng) if rng else {"start_line": 1, "start_col": 1, "end_line": 1, "end_col": 1}
        new_text = getattr(e, "newText", e.get("newText", "") if isinstance(e, dict) else "")
        return {
            "start_line": range_dict["start_line"],
            "start_col": range_dict["start_col"],
            "end_line": range_dict["end_line"],
            "end_col": range_dict["end_col"],
            "new_text": new_text,
        }

    @staticmethod
    def _format_code_action(a: Any) -> dict[str, Any]:
        title = getattr(a, "title", a.get("title", "") if isinstance(a, dict) else "")
        kind = getattr(a, "kind", None) if hasattr(a, "kind") else (a.get("kind") if isinstance(a, dict) else None)
        if hasattr(kind, "value"):
            kind = kind.value
        return {
            "title": title,
            "kind": str(kind) if kind else None,
            "is_preferred": getattr(a, "is_preferred", a.get("is_preferred") if isinstance(a, dict) else False),
            "has_edit": bool(getattr(a, "edit", None) or (a.get("edit") if isinstance(a, dict) else None)),
            "has_command": bool(getattr(a, "command", None) or (a.get("command") if isinstance(a, dict) else None)),
        }

    @staticmethod
    def _format_signature_help(h: Any) -> dict[str, Any]:
        if not h:
            return {"signatures": [], "active_signature": 0, "active_parameter": 0}
        signatures = []
        sigs = getattr(h, "signatures", h.get("signatures", []) if isinstance(h, dict) else [])
        for sig in sigs:
            label = getattr(sig, "label", sig.get("label", "") if isinstance(sig, dict) else "")
            doc = getattr(sig, "documentation", sig.get("documentation", "") if isinstance(sig, dict) else "")
            if hasattr(doc, "value"):
                doc = doc.value
            params = []
            for p in getattr(sig, "parameters", sig.get("parameters", []) if isinstance(sig, dict) else []):
                p_label = getattr(p, "label", p.get("label", "") if isinstance(p, dict) else "")
                p_doc = getattr(p, "documentation", p.get("documentation", "") if isinstance(p, dict) else "")
                if hasattr(p_doc, "value"):
                    p_doc = p_doc.value
                params.append({"label": p_label, "documentation": p_doc})
            signatures.append({"label": label, "documentation": doc, "parameters": params})
        active_sig = getattr(h, "activeSignature", h.get("activeSignature", 0) if isinstance(h, dict) else 0)
        active_param = getattr(h, "activeParameter", h.get("activeParameter", 0) if isinstance(h, dict) else 0)
        return {"signatures": signatures, "active_signature": active_sig, "active_parameter": active_param}

    @staticmethod
    def _format_document_highlight(h: Any) -> dict[str, Any]:
        kind = getattr(h, "kind", None) if hasattr(h, "kind") else (h.get("kind") if isinstance(h, dict) else None)
        if hasattr(kind, "value"):
            kind = kind.value
        kind_map = {1: "Text", 2: "Read", 3: "Write"}
        rng = getattr(h, "range", None) or (h.get("range") if isinstance(h, dict) else None)
        range_dict = LspClient._format_range(rng) if rng else {"start_line": 1, "start_col": 1, "end_line": 1, "end_col": 1}
        return {
            "kind": kind_map.get(int(kind) if kind else 1, "Text"),
            "line": range_dict["start_line"],
            "col": range_dict["start_col"],
            "end_line": range_dict["end_line"],
            "end_col": range_dict["end_col"],
        }

    @staticmethod
    def _format_folding_range(r: Any) -> dict[str, Any]:
        kind = getattr(r, "kind", None) if hasattr(r, "kind") else (r.get("kind") if isinstance(r, dict) else None)
        if hasattr(kind, "value"):
            kind = kind.value
        kind_map = {1: "Comment", 2: "Imports", 3: "Region"}
        start = getattr(r, "startLine", r.get("startLine", 1) if isinstance(r, dict) else 1)
        end = getattr(r, "endLine", r.get("endLine", 1) if isinstance(r, dict) else 1)
        return {
            "start_line": int(start) + 1,
            "end_line": int(end) + 1,
            "kind": kind_map.get(int(kind) if kind else None, None),
        }

    @staticmethod
    def _format_rename_result(r: Any) -> dict[str, Any]:
        if not r:
            return {"changes": {}, "message": "No changes possible"}
        changes = {}
        docs = getattr(r, "documentChanges", r.get("documentChanges", []) if isinstance(r, dict) else [])
        for doc in (docs or []):
            uri = getattr(doc, "uri", doc.get("uri", "") if isinstance(doc, dict) else "")
            file_path = uri.replace("file://", "") if uri.startswith("file://") else uri
            edits = getattr(doc, "edits", doc.get("edits", []) if isinstance(doc, dict) else [])
            if file_path:
                if file_path not in changes:
                    changes[file_path] = []
                changes[file_path].extend([LspClient._format_text_edit(e) for e in edits])
        return {"changes": changes, "message": f"Rename changes in {len(changes)} file(s)" if changes else "No changes possible"}

    # ── Internal ───────────────────────────────────────────────────────────

    def _assert_started(self) -> None:
        if not self._started or self._client is None:
            raise LspClientError(
                f"LSP client for {self._cfg.language!r} is not started."
            )

    @property
    def language(self) -> str:
        return self._cfg.language

    @property
    def is_running(self) -> bool:
        return self._started