"""vibe/core/plugins/builtin/lsp/lsp_client.py
─────────────────────────────────────────────────────────────────────────────
Async wrapper around a single Language Server Protocol process.

Uses pygls's LanguageClient (available since pygls 1.3).  Each
LspClient instance owns one LSP subprocess for one language.

Lifecycle
─────────
    client = LspClient(cfg, root)
    await client.start()          # spawn process, send initialize/initialized
    ...
    diags  = await client.diagnostics("src/foo.py")
    items  = await client.completion("src/foo.py", line=10, col=5)
    ...
    await client.stop()           # shutdown / exit

Document synchronisation
────────────────────────
Files are opened on-demand (textDocument/didOpen) with their disk content
and a monotonically increasing version counter.  Re-opening a document that
was already opened sends a textDocument/didChange with the latest content.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from lsprotocol.types import (
    ClientCapabilities,
    ClientCompletionItemOptions,
    CompletionClientCapabilities,
    DefinitionClientCapabilities,
    DidChangeWatchedFilesClientCapabilities,
    DocumentDiagnosticParams,
    HoverClientCapabilities,
    InitializeParams,
    MarkupKind,
    PublishDiagnosticsClientCapabilities,
    ReferenceClientCapabilities,
    TextDocumentClientCapabilities,
    TextDocumentIdentifier,
    TextDocumentSyncClientCapabilities,
    TraceValue,
    WorkspaceClientCapabilities,
    WorkspaceFolder,
)
from pygls.lsp.client import BaseLanguageClient

from vibe.core.plugins.builtin.lsp.registry import LspConfig

logger = logging.getLogger(__name__)

# Seconds to wait for server to respond to requests.
_REQUEST_TIMEOUT = 10.0


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
        self._client: BaseLanguageClient | None = None
        self._open_docs: dict[str, int] = {}  # uri → version
        self._started = False

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

        self._client = BaseLanguageClient(name=self._cfg.language, version="1.0")

        try:
            await self._client.start_io(
                str(self._cfg.command[0]), *self._cfg.command[1:]
            )
        except Exception as exc:
            self._client = None
            logger.error("Failed to start LSP for %s: %s", self._cfg.language, exc)
            raise LspClientError(
                f"Failed to start LSP for {self._cfg.language!r}: {exc}"
            ) from exc

        root_uri = self._root.as_uri()
        params: InitializeParams = InitializeParams(
            capabilities=ClientCapabilities(
                text_document=TextDocumentClientCapabilities(
                    synchronization=TextDocumentSyncClientCapabilities(
                        dynamic_registration=False, did_save=True, will_save=False
                    ),
                    publish_diagnostics=PublishDiagnosticsClientCapabilities(
                        related_information=True
                    ),
                    completion=CompletionClientCapabilities(
                        completion_item=ClientCompletionItemOptions(
                            snippet_support=False
                        )
                    ),
                    hover=HoverClientCapabilities(
                        content_format=[MarkupKind.Markdown, MarkupKind.PlainText]
                    ),
                    definition=DefinitionClientCapabilities(link_support=False),
                    references=ReferenceClientCapabilities(),
                ),
                workspace=WorkspaceClientCapabilities(
                    workspace_folders=True,
                    did_change_watched_files=DidChangeWatchedFilesClientCapabilities(
                        dynamic_registration=False
                    ),
                ),
            ),
            root_uri=root_uri,
            workspace_folders=[WorkspaceFolder(uri=root_uri, name=self._root.name)],
            trace=TraceValue.Verbose,  # Enable verbose logging for LSP
        )
        await self._client.initialize_async(params)
        self._started = True
        logger.info("LSP started: %s (%s)", self._cfg.language, self._cfg.command[0])

    async def stop(self) -> None:
        """Send shutdown/exit to the server and clean up."""
        if not self._started or self._client is None:
            return
        try:
            await asyncio.wait_for(self._client.shutdown_async(None), timeout=5.0)
            self._client.exit(None)
        except Exception as exc:
            logger.debug("LSP stop error (%s): %s", self._cfg.language, exc)
        finally:
            self._started = False
            self._client = None
            self._open_docs.clear()

    # ── Document management ────────────────────────────────────────────────────

    async def _sync_document(self, file_path: str) -> str:
        """Ensure the document is open and up-to-date in the server.

        Returns the document URI.
        """
        assert self._client is not None
        path = Path(file_path).resolve()
        uri = path.as_uri()
        text = await asyncio.to_thread(
            path.read_text, encoding="utf-8", errors="replace"
        )
        lang_id = self._cfg.language_id

        if uri not in self._open_docs:
            version = 1
            self._open_docs[uri] = version
            from lsprotocol.types import DidOpenTextDocumentParams, TextDocumentItem

            open_params = DidOpenTextDocumentParams(
                text_document=TextDocumentItem(
                    uri=uri, language_id=lang_id, version=version, text=text
                )
            )
            self._client.text_document_did_open(open_params)
        else:
            version = self._open_docs[uri] + 1
            self._open_docs[uri] = version
            from lsprotocol.types import (
                DidChangeTextDocumentParams,
                TextDocumentContentChangeWholeDocument,
                VersionedTextDocumentIdentifier,
            )

            change_params = DidChangeTextDocumentParams(
                text_document=VersionedTextDocumentIdentifier(uri=uri, version=version),
                content_changes=[TextDocumentContentChangeWholeDocument(text=text)],
            )
            self._client.text_document_did_change(change_params)
        return uri

    # ── LSP capabilities ───────────────────────────────────────────────────────

    async def diagnostics(self, file_path: str) -> list[dict[str, Any]]:
        """Return diagnostics for *file_path*.

        Opens / refreshes the document and requests diagnostics from the server.
        """
        self._assert_started()
        assert self._client is not None
        uri = await self._sync_document(file_path)
        # Request diagnostics explicitly instead of waiting for publishDiagnostics
        params = DocumentDiagnosticParams(text_document=TextDocumentIdentifier(uri=uri))
        result = await asyncio.wait_for(
            self._client.text_document_diagnostic_async(params),
            timeout=_REQUEST_TIMEOUT,
        )
        diags: list[Any] = getattr(result, "items", []) if result else []
        return [self._format_diagnostic(d) for d in diags]

    async def completion(
        self, file_path: str, line: int, col: int
    ) -> list[dict[str, Any]]:
        """Return completion items at the given 1-indexed position."""
        self._assert_started()
        assert self._client is not None
        uri = await self._sync_document(file_path)
        from lsprotocol.types import (
            CompletionContext,
            CompletionParams,
            CompletionTriggerKind,
            Position,
            TextDocumentIdentifier,
        )

        params = CompletionParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=line - 1, character=col - 1),
            context=CompletionContext(
                trigger_kind=CompletionTriggerKind.Invoked, trigger_character=None
            ),
        )
        result = await asyncio.wait_for(
            self._client.text_document_completion_async(params),
            timeout=_REQUEST_TIMEOUT,
        )
        from lsprotocol.types import CompletionList

        items = result.items if isinstance(result, CompletionList) else (result or [])
        return [self._format_completion_item(i) for i in items[:50]]

    async def hover(self, file_path: str, line: int, col: int) -> dict[str, Any]:
        """Return hover information at the given 1-indexed position."""
        self._assert_started()
        assert self._client is not None
        uri = await self._sync_document(file_path)
        from lsprotocol.types import HoverParams, Position, TextDocumentIdentifier

        params = HoverParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=line - 1, character=col - 1),
        )
        result = await asyncio.wait_for(
            self._client.text_document_hover_async(params), timeout=_REQUEST_TIMEOUT
        )
        if result is None:
            return {"content": "", "kind": "plaintext"}
        from lsprotocol.types import MarkedStringWithLanguage, MarkupContent

        contents = result.contents
        if isinstance(contents, (MarkupContent, MarkedStringWithLanguage)):
            return {
                "content": contents.value,
                "kind": getattr(contents, "kind", "markdown"),
            }
        return {"content": str(contents), "kind": "plaintext"}

    async def definition(
        self, file_path: str, line: int, col: int
    ) -> list[dict[str, Any]]:
        """Return the definition location(s) of the symbol at position."""
        self._assert_started()
        assert self._client is not None
        uri = await self._sync_document(file_path)
        from lsprotocol.types import DefinitionParams, Position, TextDocumentIdentifier

        params = DefinitionParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=line - 1, character=col - 1),
        )
        result = await asyncio.wait_for(
            self._client.text_document_definition_async(params),
            timeout=_REQUEST_TIMEOUT,
        )
        if isinstance(result, list):
            locations = result
        elif result:
            locations = [result]
        else:
            locations = []
        return [self._format_location(loc) for loc in locations if loc is not None]

    async def references(
        self, file_path: str, line: int, col: int, include_declaration: bool = True
    ) -> list[dict[str, Any]]:
        """Return all references to the symbol at position."""
        self._assert_started()
        assert self._client is not None
        uri = await self._sync_document(file_path)
        from lsprotocol.types import (
            Position,
            ReferenceContext,
            ReferenceParams,
            TextDocumentIdentifier,
        )

        params = ReferenceParams(
            text_document=TextDocumentIdentifier(uri=uri),
            position=Position(line=line - 1, character=col - 1),
            context=ReferenceContext(include_declaration=include_declaration),
        )
        result = await asyncio.wait_for(
            self._client.text_document_references_async(params),
            timeout=_REQUEST_TIMEOUT,
        )
        return [self._format_location(loc) for loc in (result or [])]

    # ── Formatters ────────────────────────────────────────────────────────────

    @staticmethod
    def _format_diagnostic(d: Any) -> dict[str, Any]:
        severity_map = {1: "Error", 2: "Warning", 3: "Information", 4: "Hint"}
        sev_int = getattr(d.severity, "value", d.severity) if d.severity else None
        return {
            "severity": severity_map.get(
                int(sev_int) if isinstance(sev_int, int) else sev_int
            )
            if sev_int is not None
            else "Unknown",
            "line": d.range.start.line + 1,
            "col": d.range.start.character + 1,
            "end_line": d.range.end.line + 1,
            "end_col": d.range.end.character + 1,
            "message": d.message,
            "source": d.source or "",
            "code": str(d.code) if d.code is not None else "",
        }

    @staticmethod
    def _format_completion_item(i: Any) -> dict[str, Any]:
        doc = i.documentation
        doc_text = doc.value if hasattr(doc, "value") else str(doc or "")
        kind_map = {
            1: "Text",
            2: "Method",
            3: "Function",
            4: "Constructor",
            5: "Field",
            6: "Variable",
            7: "Class",
            8: "Interface",
            9: "Module",
            10: "Property",
            14: "Keyword",
            17: "File",
        }
        kind_int = getattr(i.kind, "value", i.kind) if i.kind else None
        return {
            "label": i.label,
            "kind": kind_map.get(
                int(kind_int) if isinstance(kind_int, int) else kind_int
            )
            if kind_int is not None
            else "Unknown",
            "detail": i.detail or "",
            "documentation": doc_text,
        }

    @staticmethod
    def _format_location(loc: Any) -> dict[str, Any]:
        uri = getattr(loc, "uri", "") or ""
        file_path = uri.replace("file://", "") if uri.startswith("file://") else uri
        return {
            "file": file_path,
            "line": loc.range.start.line + 1,
            "col": loc.range.start.character + 1,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

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
