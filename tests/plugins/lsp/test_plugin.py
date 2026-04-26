"""Tests for vibe/core/plugins/builtin/lsp/plugin.py"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.plugins.base import PluginContext, PluginMetadata
from vibe.core.plugins.builtin.lsp import plugin as lsp_plugin
from vibe.core.plugins.builtin.lsp.plugin import (
    LspPlugin,
    _BASH_FILE_RE,
    _SEVERITY_ICON,
    _WRITE_TOOLS,
    _format_diagnostics_block,
)


class TestLspPluginMetadata:
    """Tests for LspPlugin metadata."""

    def test_metadata_name_is_lsp(self):
        assert LspPlugin.metadata().name == "lsp"

    def test_metadata_priority_is_50(self):
        assert LspPlugin.metadata().priority == 50

    def test_metadata_provides_lsp_tools(self):
        metadata = LspPlugin.metadata()
        assert "lsp_diagnostics" in metadata.provides_tools
        assert "lsp_completion" in metadata.provides_tools
        assert "lsp_hover" in metadata.provides_tools
        assert "lsp_definition" in metadata.provides_tools

    def test_metadata_description_contains_lsp(self):
        desc = LspPlugin.metadata().description.lower()
        assert "lsp" in desc

    def test_metadata_tags_contain_lsp(self):
        tags = LspPlugin.metadata().tags
        assert "lsp" in tags


class TestLspPluginInitialization:
    """Tests for LspPlugin initialization."""

    def test_initializes_with_empty_clients(self):
        plugin = LspPlugin()
        assert plugin._clients == {}

    def test_initializes_with_empty_detected_languages(self):
        plugin = LspPlugin()
        assert plugin._detected_languages == set()


class TestLspPluginIsApplicable:
    """Tests for is_applicable method."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_languages_detected(self, tmp_path):
        from vibe.core.plugins.builtin.lsp.registry import detect_languages_in_dir

        context = MagicMock()
        context.workdir = tmp_path

        plugin = LspPlugin()
        result = plugin.is_applicable(context)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_python_detected(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()

        context = MagicMock()
        context.workdir = tmp_path

        plugin = LspPlugin()
        result = plugin.is_applicable(context)

        assert result is True


class TestLspPluginSetup:
    """Tests for setup method."""

    @pytest.mark.asyncio
    async def test_setup_stores_context(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()

        tool_manager = MagicMock()
        tool_manager._config = MagicMock()

        context = MagicMock()
        context.workdir = tmp_path
        context.tool_manager = tool_manager

        plugin = LspPlugin()
        await plugin.setup(context)

        assert plugin._context is context

    @pytest.mark.asyncio
    async def test_setup_detects_languages(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()

        tool_manager = MagicMock()
        tool_manager._config = MagicMock()

        context = MagicMock()
        context.workdir = tmp_path
        context.tool_manager = tool_manager

        plugin = LspPlugin()
        await plugin.setup(context)

        assert "python" in plugin._detected_languages


class TestLspPluginTeardown:
    """Tests for teardown method."""

    @pytest.mark.asyncio
    async def test_teardown_stops_all_clients(self):
        mock_client = AsyncMock()
        mock_client.stop = AsyncMock()

        plugin = LspPlugin()
        plugin._clients = {"python": mock_client}

        await plugin.teardown()

        mock_client.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_clears_clients(self):
        mock_client = AsyncMock()
        mock_client.stop = AsyncMock()

        plugin = LspPlugin()
        plugin._clients = {"python": mock_client}

        await plugin.teardown()

        assert plugin._clients == {}


class TestLspPluginExtractPath:
    """Tests for _extract_path method."""

    def test_extracts_from_path_key(self):
        plugin = LspPlugin()
        result = plugin._extract_path("read_file", {"path": "/fake/file.py"})
        assert result == "/fake/file.py"

    def test_extracts_from_file_path_key(self):
        plugin = LspPlugin()
        result = plugin._extract_path("write_file", {"file_path": "/fake/file.py"})
        assert result == "/fake/file.py"

    def test_extracts_from_filename_key(self):
        plugin = LspPlugin()
        result = plugin._extract_path("grep", {"filename": "/fake/file.py"})
        assert result == "/fake/file.py"

    def test_extracts_from_bash_command(self):
        plugin = LspPlugin()
        result = plugin._extract_path("bash", {"command": "python test.py"})
        assert result == "test.py"

    def test_returns_none_for_unknown_tool(self):
        plugin = LspPlugin()
        result = plugin._extract_path("unknown_tool", {})
        assert result is None

    def test_returns_none_for_missing_args(self):
        plugin = LspPlugin()
        result = plugin._extract_path("read_file", {})
        assert result is None


class TestLspPluginWriteTools:
    """Tests for write tool detection."""

    def test_write_tools_includes_write_file(self):
        assert "write_file" in _WRITE_TOOLS

    def test_write_tools_includes_search_replace(self):
        assert "search_replace" in _WRITE_TOOLS


class TestLspPluginBashRegex:
    """Tests for bash file extraction regex."""

    def test_matches_python_file(self):
        match = _BASH_FILE_RE.search("python test.py")
        assert match is not None
        assert match.group(2) == "test.py"

    def test_matches_typescript_file(self):
        match = _BASH_FILE_RE.search("tsc app.ts")
        assert match is not None
        assert match.group(2) == "app.ts"

    def test_matches_with_quotes(self):
        match = _BASH_FILE_RE.search("python 'test.py'")
        assert match is not None

    def test_no_match_without_file(self):
        match = _BASH_FILE_RE.search("ls -la")
        assert match is None


class TestFormatDiagnosticsBlock:
    """Tests for _format_diagnostics_block function."""

    def test_formats_empty_list(self):
        result = _format_diagnostics_block("/fake/file.py", [])
        assert "LSP diagnostics" in result
        assert "0 issues" in result

    def test_formats_single_error(self):
        diags = [
            {
                "severity": "Error",
                "line": 10,
                "col": 5,
                "message": "Undefined name 'x'",
                "source": "pylsp",
            }
        ]
        result = _format_diagnostics_block("/fake/file.py", diags)
        assert "Error" in result
        assert "Undefined name" in result
        assert "10" in result

    def test_formats_multiple_diagnostics(self):
        diags = [
            {"severity": "Error", "line": 10, "col": 5, "message": "Error 1"},
            {"severity": "Warning", "line": 20, "col": 1, "message": "Warning 1"},
        ]
        result = _format_diagnostics_block("/fake/file.py", diags)
        assert "Error" in result
        assert "Warning" in result

    def test_sorts_errors_first(self):
        diags = [
            {"severity": "Warning", "line": 20, "col": 1, "message": "Warn"},
            {"severity": "Error", "line": 10, "col": 5, "message": "Err"},
        ]
        result = _format_diagnostics_block("/fake/file.py", diags)
        error_pos = result.find("Err")
        warn_pos = result.find("Warn")
        assert error_pos < warn_pos

    def test_includes_file_path_in_header(self):
        diags = [{"severity": "Error", "line": 10, "col": 5, "message": "Err"}]
        result = _format_diagnostics_block("/path/to/file.py", diags)
        assert "file.py" in result

    def test_adds_source_tag(self):
        diags = [
            {"severity": "Error", "line": 10, "col": 5, "message": "Err", "source": "pylsp"}
        ]
        result = _format_diagnostics_block("/fake/file.py", diags)
        assert "pylsp" in result


class TestSeverityIcons:
    """Tests for severity icon mapping."""

    def test_error_icon(self):
        assert _SEVERITY_ICON["Error"] == "✗ ERROR  "

    def test_warning_icon(self):
        assert _SEVERITY_ICON["Warning"] == "⚠ WARNING"

    def test_information_icon(self):
        assert _SEVERITY_ICON["Information"] == "ℹ INFO   "

    def test_hint_icon(self):
        assert _SEVERITY_ICON["Hint"] == "· HINT   "


class TestLspPluginOnToolCall:
    """Tests for on_tool_call method."""

    @pytest.mark.asyncio
    async def test_on_tool_call_extracts_file_path(self):
        plugin = LspPlugin()
        context = MagicMock()
        context.workdir = Path("/fake")

        with patch.object(plugin, "_ensure_client_for_file", new_callable=AsyncMock):
            await plugin.on_tool_call(
                "read_file", {"path": "/fake/test.py"}, context
            )


class TestLspPluginClientManagement:
    """Tests for client management methods."""

    @pytest.mark.asyncio
    async def test_client_for_file_returns_none_for_unknown(self):
        plugin = LspPlugin()
        result = plugin._client_for_file("/fake/unknown.xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_ensure_client_for_file_starts_new_client(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()

        plugin = LspPlugin()
        plugin._detected_languages = {"python"}

        context = MagicMock()
        context.workdir = tmp_path
        context.tool_manager = MagicMock()
        context.tool_manager._config = MagicMock()

        with patch.object(plugin, "_start_client", new_callable=AsyncMock) as mock_start:
            await plugin._ensure_client_for_file("/fake/test.py", tmp_path)


class TestLspPluginFormatting:
    """Tests for formatting utilities."""

    def test_format_diagnostics_block_with_all_severities(self):
        diags = [
            {"severity": "Error", "line": 1, "col": 1, "message": "e1"},
            {"severity": "Warning", "line": 2, "col": 2, "message": "w1"},
            {"severity": "Information", "line": 3, "col": 3, "message": "i1"},
            {"severity": "Hint", "line": 4, "col": 4, "message": "h1"},
        ]
        result = _format_diagnostics_block("/fake/file.py", diags)
        assert "✗ ERROR" in result
        assert "⚠ WARNING" in result
        assert "ℹ INFO" in result
        assert "· HINT" in result

    def test_format_diagnostics_block_counts_correctly(self):
        diags = [
            {"severity": "Error", "line": 1, "col": 1, "message": "e1"},
            {"severity": "Error", "line": 2, "col": 2, "message": "e2"},
            {"severity": "Warning", "line": 3, "col": 3, "message": "w1"},
        ]
        result = _format_diagnostics_block("/fake/file.py", diags)
        assert "2 errors" in result
        assert "1 warning" in result

    def test_format_diagnostics_block_no_errors_no_warnings(self):
        diags = [
            {"severity": "Hint", "line": 1, "col": 1, "message": "h1"},
        ]
        result = _format_diagnostics_block("/fake/file.py", diags)
        assert "other" in result.lower() or "hint" in result.lower()


class TestLspPluginClass:
    """Integration tests for LspPlugin class."""

    def test_inherits_from_tool_event_plugin(self):
        assert issubclass(LspPlugin, object)

    def test_can_create_instance(self):
        plugin = LspPlugin()
        assert plugin is not None

    @pytest.mark.asyncio
    async def test_setup_is_async(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()

        tool_manager = MagicMock()
        tool_manager._config = MagicMock()

        context = MagicMock()
        context.workdir = tmp_path
        context.tool_manager = tool_manager

        plugin = LspPlugin()
        assert hasattr(plugin.setup, "__aenter__")

    @pytest.mark.asyncio
    async def test_teardown_is_async(self):
        plugin = LspPlugin()
        assert hasattr(plugin.teardown, "__aenter__")


class TestLspPluginAttributes:
    """Tests for plugin attributes."""

    def test_has_clients_attribute(self):
        plugin = LspPlugin()
        assert hasattr(plugin, "_clients")

    def test_has_detected_languages_attribute(self):
        plugin = LspPlugin()
        assert hasattr(plugin, "_detected_languages")

    def test_has_pending_diag_files_attribute(self):
        plugin = LspPlugin()
        assert hasattr(plugin, "_pending_diag_files")

    def test_has_context_attribute(self):
        plugin = LspPlugin()
        assert hasattr(plugin, "_context")


class TestLspPluginErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_on_tool_result_handles_no_client(self):
        plugin = LspPlugin()
        plugin._pending_diag_files = ["/fake/test.py"]

        context = MagicMock()
        context.extra = {}

        await plugin.on_tool_result(
            "write_file",
            {"path": "/fake/test.py"},
            "result",
            context,
        )

    @pytest.mark.asyncio
    async def test_on_tool_result_handles_exception(self):
        mock_client = AsyncMock()
        mock_client.diagnostics = AsyncMock(side_effect=Exception("test error"))

        plugin = LspPlugin()
        plugin._clients = {"python": mock_client}
        plugin._pending_diag_files = ["/fake/test.py"]

        context = MagicMock()
        context.extra = {}

        await plugin.on_tool_result(
            "write_file",
            {"path": "/fake/test.py"},
            "result",
            context,
        )


class TestLspPluginEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_setup_with_no_detected_languages(self, tmp_path):
        context = MagicMock()
        context.workdir = tmp_path
        context.tool_manager = MagicMock()
        context.tool_manager._config = MagicMock()

        plugin = LspPlugin()
        await plugin.setup(context)

    def test_extract_path_handles_invalid_args(self):
        plugin = LspPlugin()
        result = plugin._extract_path("read_file", {"path": 123})
        assert result is None

    def test_extract_path_handles_empty_string(self):
        plugin = LspPlugin()
        result = plugin._extract_path("read_file", {"path": ""})
        assert result is None

    @pytest.mark.asyncio
    async def test_teardown_with_no_clients(self):
        plugin = LspPlugin()
        plugin._clients = {}
        await plugin.teardown()
        assert plugin._clients == {}


class TestLspPluginDiagnosticsOutput:
    """Tests for diagnostics output in context.extra."""

    @pytest.mark.asyncio
    async def test_on_tool_result_sets_output_in_context_extra(self):
        mock_client = AsyncMock()
        mock_client.diagnostics = AsyncMock(
            return_value=[
                {
                    "severity": "Error",
                    "line": 1,
                    "col": 1,
                    "message": "Error",
                    "source": "test",
                }
            ]
        )
        mock_client.is_running = True

        plugin = LspPlugin()
        plugin._clients = {"python": mock_client}
        plugin._pending_diag_files = ["/fake/test.py"]

        context = MagicMock()
        context.extra = {}

        await plugin.on_tool_result(
            "write_file",
            {"path": "/fake/test.py"},
            "result",
            context,
        )

        assert "lsp_diagnostics_output" in context.extra


class TestLspPluginRegisterTools:
    """Tests for tool registration."""

    @pytest.mark.asyncio
    async def test_register_tools_with_no_tool_manager(self):
        context = MagicMock()
        context.workdir = Path("/fake")
        context.tool_manager = None

        plugin = LspPlugin()
        plugin._register_tools(context)