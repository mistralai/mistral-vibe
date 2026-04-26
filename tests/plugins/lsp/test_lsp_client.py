"""Tests for vibe/core/plugins/builtin/lsp/lsp_client.py"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.plugins.builtin.lsp.lsp_client import (
    LspClient,
    LspClientError,
    _LSP_CLIENTS,
)


class TestLspClientError:
    """Tests for LspClientError exception."""

    def test_inherits_from_runtime_error(self):
        assert issubclass(LspClientError, RuntimeError)


class TestLspClientLifecycle:
    """Tests for LspClient initialization and lifecycle."""

    def test_initializes_with_config_and_root(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        assert client._cfg == cfg
        assert client._root == root
        assert client.language == "python"
        assert client.is_running is False

    @pytest.mark.asyncio
    async def test_start_raises_when_executable_not_available(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="nonexistent",
            extensions=frozenset({".nonexistent"}),
            command=["nonexistent-lsp-12345"],
            language_id="nonexistent",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not found"):
            await client.start()

    @pytest.mark.asyncio
    async def test_start_raises_when_language_not_supported(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="unsupported",
            extensions=frozenset({".unsupported"}),
            command=["unsupported-lsp"],
            language_id="unsupported",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="No lsp-client for language"):
            await client.start()

    @pytest.mark.asyncio
    async def test_start_sets_started_flag(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()

            assert client.is_running is True

    @pytest.mark.asyncio
    async def test_stop_clears_started_flag(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()
            assert client.is_running is True

            await client.stop()
            assert client.is_running is False

    @pytest.mark.asyncio
    async def test_stop_does_nothing_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        await client.stop()
        assert client.is_running is False


class TestLspClientDiagnostics:
    """Tests for diagnostics method."""

    @pytest.mark.asyncio
    async def test_diagnostics_raises_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.diagnostics("/fake/file.py")

    @pytest.mark.asyncio
    async def test_diagnostics_returns_empty_list_on_error(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client.request_text_document_diagnostics = AsyncMock(
                side_effect=Exception("LSP error")
            )
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()
            result = await client.diagnostics("/fake/file.py")

            assert result == []

    @pytest.mark.asyncio
    async def test_diagnostics_returns_formatted_results(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        mock_diag = MagicMock()
        mock_diag.severity = MagicMock(value=1)
        mock_diag.range = MagicMock()
        mock_diag.range.start = MagicMock()
        mock_diag.range.start.line = 0
        mock_diag.range.start.character = 0
        mock_diag.range.end = MagicMock()
        mock_diag.range.end.line = 0
        mock_diag.range.end.character = 5
        mock_diag.message = "Test error"

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client.request_text_document_diagnostics = AsyncMock(
                return_value=[mock_diag]
            )
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()
            result = await client.diagnostics("/fake/file.py")

            assert len(result) == 1
            assert result[0]["severity"] == "Error"
            assert result[0]["line"] == 1
            assert result[0]["col"] == 1
            assert result[0]["message"] == "Test error"


class TestLspClientCompletion:
    """Tests for completion method."""

    @pytest.mark.asyncio
    async def test_completion_raises_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.completion("/fake/file.py", 1, 1)

    @pytest.mark.asyncio
    async def test_completion_returns_empty_list_on_error(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client.request_completion = AsyncMock(
                side_effect=Exception("LSP error")
            )
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()
            result = await client.completion("/fake/file.py", 1, 1)

            assert result == []

    @pytest.mark.asyncio
    async def test_completion_converts_to_0_indexed(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        mock_item = MagicMock()
        mock_item.label = "test"
        mock_item.kind = None
        mock_item.detail = "detail"

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client.request_completion = AsyncMock(return_value=[mock_item])
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()
            result = await client.completion("/fake/file.py", 5, 10)

            mock_client.request_completion.assert_called_once()
            call_args = mock_client.request_completion.call_args
            position = call_args.kwargs["position"]
            assert position.line == 4
            assert position.character == 9


class TestLspClientHover:
    """Tests for hover method."""

    @pytest.mark.asyncio
    async def test_hover_returns_default_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        result = await client.hover("/fake/file.py", 1, 1)

        assert result == {"content": "", "kind": "plaintext"}

    @pytest.mark.asyncio
    async def test_hover_returns_formatted_content(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        mock_result = MagicMock()
        mock_result.contents = "Hover content"

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client.request_hover = AsyncMock(return_value=mock_result)
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()
            result = await client.hover("/fake/file.py", 1, 1)

            assert result["content"] == "Hover content"


class TestLspClientDefinition:
    """Tests for definition method."""

    @pytest.mark.asyncio
    async def test_definition_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.definition("/fake/file.py", 1, 1)

    @pytest.mark.asyncio
    async def test_definition_returns_formatted_location(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        root = Path("/fake/root")
        client = LspClient(cfg, root)

        mock_loc = MagicMock()
        mock_loc.uri = "file:///fake/definition.py"
        mock_loc.range = MagicMock()
        mock_loc.range.start = MagicMock()
        mock_loc.range.start.line = 10
        mock_loc.range.start.character = 5
        mock_loc.range.end = MagicMock()
        mock_loc.range.end.line = 10
        mock_loc.range.end.character = 5

        with patch("vibe.core.plugins.builtin.lsp.lsp_client.clients") as mock_clients:
            mock_client_class = MagicMock()
            mock_client = MagicMock()
            mock_client.request_definition_locations = AsyncMock(return_value=[mock_loc])
            mock_client_class.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=None)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_clients.BasedpyrightClient = mock_client_class

            await client.start()
            result = await client.definition("/fake/file.py", 1, 1)

            assert len(result) == 1
            assert result[0]["file"] == "/fake/definition.py"
            assert result[0]["line"] == 11
            assert result[0]["col"] == 6


class TestLspClientReferences:
    """Tests for references method."""

    @pytest.mark.asyncio
    async def test_references_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.references("/fake/file.py", 1, 1)


class TestLspClientDocumentSymbols:
    """Tests for document_symbols method."""

    @pytest.mark.asyncio
    async def test_document_symbols_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.document_symbols("/fake/file.py")


class TestLspClientWorkspaceSymbols:
    """Tests for workspace_symbols method."""

    @pytest.mark.asyncio
    async def test_workspace_symbols_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.workspace_symbols("query")


class TestLspClientRename:
    """Tests for rename method."""

    @pytest.mark.asyncio
    async def test_rename_returns_empty_dict_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.rename("/fake/file.py", 1, 1, "new_name")


class TestLspClientFormatting:
    """Tests for formatting methods."""

    @pytest.mark.asyncio
    async def test_formatting_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.formatting("/fake/file.py")

    @pytest.mark.asyncio
    async def test_range_formatting_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.range_formatting("/fake/file.py", 1, 1, 10, 5)


class TestLspClientCodeActions:
    """Tests for code_actions method."""

    @pytest.mark.asyncio
    async def test_code_actions_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.code_actions("/fake/file.py", 1, 1)


class TestLspClientSignatureHelp:
    """Tests for signature_help method."""

    @pytest.mark.asyncio
    async def test_signature_help_returns_empty_dict_when_not_started(
        self,
    ):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.signature_help("/fake/file.py", 1, 1)


class TestLspClientDocumentHighlight:
    """Tests for document_highlight method."""

    @pytest.mark.asyncio
    async def test_document_highlight_returns_empty_list_when_not_started(
        self,
    ):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.document_highlight("/fake/file.py", 1, 1)


class TestLspClientFoldingRanges:
    """Tests for folding_ranges method."""

    @pytest.mark.asyncio
    async def test_folding_ranges_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.folding_ranges("/fake/file.py")


class TestLspClientImplementation:
    """Tests for implementation method."""

    @pytest.mark.asyncio
    async def test_implementation_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.implementation("/fake/file.py", 1, 1)


class TestLspClientTypeDefinition:
    """Tests for type_definition method."""

    @pytest.mark.asyncio
    async def test_type_definition_returns_empty_list_when_not_started(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        with pytest.raises(LspClientError, match="not started"):
            await client.type_definition("/fake/file.py", 1, 1)


class TestLspClientFormatters:
    """Tests for formatting helper methods."""

    def test_format_diagnostic_handles_all_severity_levels(self):
        test_cases = [
            (1, "Error"),
            (2, "Warning"),
            (3, "Information"),
            (4, "Hint"),
        ]
        for severity, expected in test_cases:
            mock_diag = MagicMock()
            mock_diag.severity = severity
            mock_diag.range = None
            mock_diag.message = "test"

            result = LspClient._format_diagnostic(mock_diag)
            assert result["severity"] == expected

    def test_format_completion_item_maps_all_kinds(self):
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
        for kind, expected in kind_map.items():
            mock_item = MagicMock()
            mock_item.kind = kind
            mock_item.label = "test"
            mock_item.detail = ""
            mock_item.documentation = ""

            result = LspClient._format_completion_item(mock_item)
            assert result["kind"] == expected

    def test_format_location_handles_file_uri(self):
        mock_loc = MagicMock()
        mock_loc.uri = "file:///path/to/file.py"
        mock_loc.range = MagicMock()
        mock_loc.range.start = MagicMock()
        mock_loc.range.start.line = 5
        mock_loc.range.start.character = 3
        mock_loc.range.end = MagicMock()
        mock_loc.range.end.line = 5
        mock_loc.range.end.character = 3

        result = LspClient._format_location(mock_loc)
        assert result["file"] == "/path/to/file.py"
        assert result["line"] == 6
        assert result["col"] == 4

    def test_format_range_converts_to_1_indexed(self):
        mock_rng = MagicMock()
        mock_rng.start = MagicMock()
        mock_rng.start.line = 0
        mock_rng.start.character = 0
        mock_rng.end = MagicMock()
        mock_rng.end.line = 10
        mock_rng.end.character = 5

        result = LspClient._format_range(mock_rng)
        assert result["start_line"] == 1
        assert result["start_col"] == 1
        assert result["end_line"] == 11
        assert result["end_col"] == 6

    def test_format_document_symbol_extracts_all_fields(self):
        mock_symbol = MagicMock()
        mock_symbol.name = "TestClass"
        mock_symbol.kind = 7
        mock_symbol.detail = "class"
        mock_symbol.range = MagicMock()
        mock_symbol.range.start = MagicMock()
        mock_symbol.range.start.line = 0
        mock_symbol.range.start.character = 0
        mock_symbol.range.end = MagicMock()
        mock_symbol.range.end.line = 0
        mock_symbol.range.end.character = 10

        result = LspClient._format_document_symbol(mock_symbol)
        assert result["name"] == "TestClass"
        assert result["kind"] == "Class"
        assert result["detail"] == "class"

    def test_format_code_action_extracts_title_and_kind(self):
        mock_action = MagicMock()
        mock_action.title = "Remove unused import"
        mock_action.kind = "quickfix"
        mock_action.is_preferred = True
        mock_action.edit = None
        mock_action.command = None

        result = LspClient._format_code_action(mock_action)
        assert result["title"] == "Remove unused import"
        assert result["is_preferred"] is True

    def test_format_signature_help_handles_no_result(self):
        result = LspClient._format_signature_help(None)
        assert result == {
            "signatures": [],
            "active_signature": 0,
            "active_parameter": 0,
        }

    def test_format_folding_range_maps_kinds(self):
        mock_range = MagicMock()
        mock_range.kind = 1
        mock_range.startLine = 0
        mock_range.endLine = 10

        result = LspClient._format_folding_range(mock_range)
        assert result["kind"] == "Comment"
        assert result["start_line"] == 1
        assert result["end_line"] == 11

    def test_format_rename_result_handles_empty_result(self):
        result = LspClient._format_rename_result(None)
        assert result == {"changes": {}, "message": "No changes possible"}

    def test_format_rename_result_formats_changes(self):
        mock_edit = MagicMock()
        mock_edit.range = MagicMock()
        mock_edit.range.start = MagicMock()
        mock_edit.range.start.line = 0
        mock_edit.range.start.character = 0
        mock_edit.range.end = MagicMock()
        mock_edit.range.end.line = 0
        mock_edit.range.end.character = 5
        mock_edit.newText = "new_text"

        mock_doc = MagicMock()
        mock_doc.uri = "file:///path/to/file.py"
        mock_doc.edits = [mock_edit]

        mock_result = MagicMock()
        mock_result.documentChanges = [mock_doc]

        result = LspClient._format_rename_result(mock_result)
        assert "changes" in result


class TestLspClientLanguageProperty:
    """Tests for language property."""

    def test_language_returns_config_language(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        assert client.language == "python"

    def test_is_running_returns_started_flag(self):
        from vibe.core.plugins.builtin.lsp.registry import LspConfig

        cfg = LspConfig(
            language="python",
            extensions=frozenset({".py"}),
            command=["python"],
            language_id="python",
        )
        client = LspClient(cfg, Path("/fake/root"))

        assert client.is_running is False