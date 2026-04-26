"""Tests for vibe/core/plugins/builtin/lsp/tools.py"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.plugins.builtin.lsp import tools as lsp_tools
from vibe.core.plugins.builtin.lsp.registry import LspConfig
from vibe.core.plugins.builtin.lsp.tools import (
    LspCodeActionArgs,
    LspCodeActionResult,
    LspCompletionArgs,
    LspCompletionResult,
    LspDefinitionArgs,
    LspDefinitionResult,
    LspDiagnosticsArgs,
    LspDiagnosticsResult,
    LspDocumentHighlightArgs,
    LspDocumentSymbolsArgs,
    LspDocumentSymbolsResult,
    LspFormattingArgs,
    LspHoverArgs,
    LspHoverResult,
    LspImplementationArgs,
    LspImplementationResult,
    LspRangeFormattingArgs,
    LspReferencesArgs,
    LspReferencesResult,
    LspRenameArgs,
    LspRenameResult,
    LspSignatureHelpArgs,
    LspSignatureHelpResult,
    LspStatusArgs,
    LspStatusResult,
    LspTypeDefinitionArgs,
    LspTypeDefinitionResult,
    LspWorkspaceSymbolsArgs,
    LspWorkspaceSymbolsResult,
    make_lsp_tools,
)


@pytest.fixture
def mock_clients() -> dict:
    return {}


@pytest.fixture
def mock_config() -> MagicMock:
    config = MagicMock()
    return config


@pytest.fixture
def mock_state() -> MagicMock:
    state = MagicMock()
    return state


class TestLspDiagnosticsTool:
    """Tests for LspDiagnosticsTool."""

    def test_name_is_diagnostics(self):
        from vibe.core.plugins.builtin.lsp.tools import LspDiagnosticsTool

        assert LspDiagnosticsTool.name == "lsp_diagnostics"

    def test_description_contains_diagnostics(self):
        from vibe.core.plugins.builtin.lsp.tools import LspDiagnosticsTool

        assert "diagnostics" in LspDiagnosticsTool.description.lower()

    def test_get_tool_prompt_returns_string(self):
        from vibe.core.plugins.builtin.lsp.tools import LspDiagnosticsTool

        prompt = LspDiagnosticsTool.get_tool_prompt()
        assert prompt is not None
        assert "diagnostics" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspDiagnosticsTool

        tool = LspDiagnosticsTool(mock_config, mock_state)
        args = LspDiagnosticsArgs(file_path="/fake/test.py")

        results = list(tool.run(args))
        assert len(results) >= 1

        result = results[-1]
        assert isinstance(result, LspDiagnosticsResult)
        assert "not running" in result.message.lower() or "not configured" in result.message.lower()


class TestLspCompletionTool:
    """Tests for LspCompletionTool."""

    def test_name_is_completion(self):
        from vibe.core.plugins.builtin.lsp.tools import LspCompletionTool

        assert LspCompletionTool.name == "lsp_completion"

    def test_get_tool_prompt_returns_string(self):
        from vibe.core.plugins.builtin.lsp.tools import LspCompletionTool

        prompt = LspCompletionTool.get_tool_prompt()
        assert prompt is not None
        assert "completion" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspCompletionTool

        tool = LspCompletionTool(mock_config, mock_state)
        args = LspCompletionArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1

        result = results[-1]
        assert isinstance(result, LspCompletionResult)
        assert "not running" in result.message.lower() or "not configured" in result.message.lower()


class TestLspHoverTool:
    """Tests for LspHoverTool."""

    def test_name_is_hover(self):
        from vibe.core.plugins.builtin.lsp.tools import LspHoverTool

        assert LspHoverTool.name == "lsp_hover"

    def test_get_tool_prompt_returns_string(self):
        from vibe.core.plugins.builtin.lsp.tools import LspHoverTool

        prompt = LspHoverTool.get_tool_prompt()
        assert prompt is not None
        assert "hover" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspHoverTool

        tool = LspHoverTool(mock_config, mock_state)
        args = LspHoverArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1

        result = results[-1]
        assert isinstance(result, LspHoverResult)
        assert "not running" in result.message.lower() or "not configured" in result.message.lower()


class TestLspDefinitionTool:
    """Tests for LspDefinitionTool."""

    def test_name_is_definition(self):
        from vibe.core.plugins.builtin.lsp.tools import LspDefinitionTool

        assert LspDefinitionTool.name == "lsp_definition"

    def test_get_tool_prompt_returns_string(self):
        from vibe.core.plugins.builtin.lsp.tools import LspDefinitionTool

        prompt = LspDefinitionTool.get_tool_prompt()
        assert prompt is not None
        assert "definition" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspDefinitionTool

        tool = LspDefinitionTool(mock_config, mock_state)
        args = LspDefinitionArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1

        result = results[-1]
        assert isinstance(result, LspDefinitionResult)
        assert "not running" in result.message.lower() or "not configured" in result.message.lower()


class TestLspReferencesTool:
    """Tests for LspReferencesTool."""

    def test_name_is_references(self):
        from vibe.core.plugins.builtin.lsp.tools import LspReferencesTool

        assert LspReferencesTool.name == "lsp_references"

    def test_get_tool_prompt_returns_string(self):
        from vibe.core.plugins.builtin.lsp.tools import LspReferencesTool

        prompt = LspReferencesTool.get_tool_prompt()
        assert prompt is not None
        assert "references" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspReferencesTool

        tool = LspReferencesTool(mock_config, mock_state)
        args = LspReferencesArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1

        result = results[-1]
        assert isinstance(result, LspReferencesResult)
        assert "not running" in result.message.lower() or "not configured" in result.message.lower()


class TestLspStatusTool:
    """Tests for LspStatusTool."""

    def test_name_is_status(self):
        from vibe.core.plugins.builtin.lsp.tools import LspStatusTool

        assert LspStatusTool.name == "lsp_status"

    def test_get_tool_prompt_returns_string(self):
        from vibe.core.plugins.builtin.lsp.tools import LspStatusTool

        prompt = LspStatusTool.get_tool_prompt()
        assert prompt is not None
        assert "status" in prompt.lower()

    @pytest.mark.asyncio
    async def test_run_returns_status(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = {}
        lsp_tools._LSP_DETECTED_LANGUAGES = set()
        lsp_tools._LSP_WORKDIR = "/fake/workdir"

        from vibe.core.plugins.builtin.lsp.tools import LspStatusTool

        tool = LspStatusTool(mock_config, mock_state)
        args = LspStatusArgs()

        results = list(tool.run(args))
        assert len(results) >= 1

        result = results[-1]
        assert isinstance(result, LspStatusResult)
        assert "workdir" in result.status


class TestLspDocumentSymbolsTool:
    """Tests for LspDocumentSymbolsTool."""

    def test_name_is_document_symbols(self):
        from vibe.core.plugins.builtin.lsp.tools import LspDocumentSymbolsTool

        assert LspDocumentSymbolsTool.name == "lsp_document_symbols"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspDocumentSymbolsTool

        tool = LspDocumentSymbolsTool(mock_config, mock_state)
        args = LspDocumentSymbolsArgs(file_path="/fake/test.py")

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspWorkspaceSymbolsTool:
    """Tests for LspWorkspaceSymbolsTool."""

    def test_name_is_workspace_symbols(self):
        from vibe.core.plugins.builtin.lsp.tools import LspWorkspaceSymbolsTool

        assert LspWorkspaceSymbolsTool.name == "lsp_workspace_symbols"

    @pytest.mark.asyncio
    async def test_run_returns_no_clients_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = {}
        lsp_tools._LSP_DETECTED_LANGUAGES = set()

        from vibe.core.plugins.builtin.lsp.tools import LspWorkspaceSymbolsTool

        tool = LspWorkspaceSymbolsTool(mock_config, mock_state)
        args = LspWorkspaceSymbolsArgs(query="test")

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspSignatureHelpTool:
    """Tests for LspSignatureHelpTool."""

    def test_name_is_signature_help(self):
        from vibe.core.plugins.builtin.lsp.tools import LspSignatureHelpTool

        assert LspSignatureHelpTool.name == "lsp_signature_help"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspSignatureHelpTool

        tool = LspSignatureHelpTool(mock_config, mock_state)
        args = LspSignatureHelpArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspCodeActionTool:
    """Tests for LspCodeActionTool."""

    def test_name_is_code_action(self):
        from vibe.core.plugins.builtin.lsp.tools import LspCodeActionTool

        assert LspCodeActionTool.name == "lsp_code_action"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspCodeActionTool

        tool = LspCodeActionTool(mock_config, mock_state)
        args = LspCodeActionArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspFormattingTool:
    """Tests for LspFormattingTool."""

    def test_name_is_formatting(self):
        from vibe.core.plugins.builtin.lsp.tools import LspFormattingTool

        assert LspFormattingTool.name == "lsp_formatting"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspFormattingTool

        tool = LspFormattingTool(mock_config, mock_state)
        args = LspFormattingArgs(file_path="/fake/test.py")

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspRangeFormattingTool:
    """Tests for LspRangeFormattingTool."""

    def test_name_is_range_formatting(self):
        from vibe.core.plugins.builtin.lsp.tools import LspRangeFormattingTool

        assert LspRangeFormattingTool.name == "lsp_range_formatting"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspRangeFormattingTool

        tool = LspRangeFormattingTool(mock_config, mock_state)

        from pydantic import Field
        from vibe.core.plugins.builtin.lsp.tools import LspRangeFormattingArgs

        args = LspRangeFormattingArgs(
            file_path="/fake/test.py",
            start_line=1,
            start_col=1,
            end_line=10,
            end_col=5,
        )

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspDocumentHighlightTool:
    """Tests for LspDocumentHighlightTool."""

    def test_name_is_document_highlight(self):
        from vibe.core.plugins.builtin.lsp.tools import LspDocumentHighlightTool

        assert LspDocumentHighlightTool.name == "lsp_document_highlight"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspDocumentHighlightTool

        tool = LspDocumentHighlightTool(mock_config, mock_state)
        args = LspDocumentHighlightArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspRenameTool:
    """Tests for LspRenameTool."""

    def test_name_is_rename(self):
        from vibe.core.plugins.builtin.lsp.tools import LspRenameTool

        assert LspRenameTool.name == "lsp_rename"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspRenameTool

        tool = LspRenameTool(mock_config, mock_state)
        args = LspRenameArgs(file_path="/fake/test.py", line=1, col=1, new_name="new_name")

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspImplementationTool:
    """Tests for LspImplementationTool."""

    def test_name_is_implementation(self):
        from vibe.core.plugins.builtin.lsp.tools import LspImplementationTool

        assert LspImplementationTool.name == "lsp_implementation"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspImplementationTool

        tool = LspImplementationTool(mock_config, mock_state)
        args = LspImplementationArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1


class TestLspTypeDefinitionTool:
    """Tests for LspTypeDefinitionTool."""

    def test_name_is_type_definition(self):
        from vibe.core.plugins.builtin.lsp.tools import LspTypeDefinitionTool

        assert LspTypeDefinitionTool.name == "lsp_type_definition"

    @pytest.mark.asyncio
    async def test_run_returns_no_client_message(self, mock_clients, mock_config, mock_state):
        lsp_tools._LSP_CLIENTS = mock_clients

        from vibe.core.plugins.builtin.lsp.tools import LspTypeDefinitionTool

        tool = LspTypeDefinitionTool(mock_config, mock_state)
        args = LspTypeDefinitionArgs(file_path="/fake/test.py", line=1, col=1)

        results = list(tool.run(args))
        assert len(results) >= 1


class TestMakeLspTools:
    """Tests for make_lsp_tools factory function."""

    def test_returns_list_of_tools(self, mock_config, mock_state):
        clients = {}
        detected = {"python"}
        workdir = "/fake/workdir"

        tools = make_lsp_tools(mock_config, mock_state, clients, detected, workdir)

        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_contains_diagnostics_tool(self, mock_config, mock_state):
        clients = {}
        detected = {"python"}
        workdir = "/fake/workdir"

        tools = make_lsp_tools(mock_config, mock_state, clients, detected, workdir)

        tool_names = [t.name for t in tools]
        assert "lsp_diagnostics" in tool_names

    def test_contains_completion_tool(self, mock_config, mock_state):
        clients = {}
        detected = {"python"}
        workdir = "/fake/workdir"

        tools = make_lsp_tools(mock_config, mock_state, clients, detected, workdir)

        tool_names = [t.name for t in tools]
        assert "lsp_completion" in tool_names

    def test_contains_hover_tool(self, mock_config, mock_state):
        clients = {}
        detected = {"python"}
        workdir = "/fake/workdir"

        tools = make_lsp_tools(mock_config, mock_state, clients, detected, workdir)

        tool_names = [t.name for t in tools]
        assert "lsp_hover" in tool_names

    def test_contains_status_tool(self, mock_config, mock_state):
        clients = {}
        detected = {"python"}
        workdir = "/fake/workdir"

        tools = make_lsp_tools(mock_config, mock_state, clients, detected, workdir)

        tool_names = [t.name for t in tools]
        assert "lsp_status" in tool_names

    def test_sets_global_state(self, mock_config, mock_state):
        clients = {"python": MagicMock()}
        detected = {"python"}
        workdir = "/fake/workdir"

        make_lsp_tools(mock_config, mock_state, clients, detected, workdir)

        assert lsp_tools._LSP_CLIENTS == clients
        assert lsp_tools._LSP_DETECTED_LANGUAGES == detected
        assert lsp_tools._LSP_WORKDIR == workdir


class TestLspToolsDescriptions:
    """Tests that all LSP tools have proper descriptions."""

    @pytest.mark.parametrize(
        "tool_class,expected_name",
        [
            ("LspDiagnosticsTool", "lsp_diagnostics"),
            ("LspCompletionTool", "lsp_completion"),
            ("LspHoverTool", "lsp_hover"),
            ("LspDefinitionTool", "lsp_definition"),
            ("LspReferencesTool", "lsp_references"),
            ("LspStatusTool", "lsp_status"),
            ("LspDocumentSymbolsTool", "lsp_document_symbols"),
            ("LspWorkspaceSymbolsTool", "lsp_workspace_symbols"),
            ("LspSignatureHelpTool", "lsp_signature_help"),
            ("LspCodeActionTool", "lsp_code_action"),
            ("LspFormattingTool", "lsp_formatting"),
            ("LspRangeFormattingTool", "lsp_range_formatting"),
            ("LspDocumentHighlightTool", "lsp_document_highlight"),
            ("LspRenameTool", "lsp_rename"),
            ("LspImplementationTool", "lsp_implementation"),
            ("LspTypeDefinitionTool", "lsp_type_definition"),
        ],
    )
    def test_tool_has_correct_name(self, tool_class, expected_name):
        tool_cls = getattr(lsp_tools, tool_class)
        assert tool_cls.name == expected_name

    @pytest.mark.parametrize(
        "tool_class",
        [
            "LspDiagnosticsTool",
            "LspCompletionTool",
            "LspHoverTool",
            "LspDefinitionTool",
            "LspReferencesTool",
            "LspStatusTool",
            "LspDocumentSymbolsTool",
            "LspWorkspaceSymbolsTool",
            "LspSignatureHelpTool",
            "LspCodeActionTool",
            "LspFormattingTool",
            "LspRangeFormattingTool",
            "LspDocumentHighlightTool",
            "LspRenameTool",
            "LspImplementationTool",
            "LspTypeDefinitionTool",
        ],
    )
    def test_tool_has_description(self, tool_class):
        tool_cls = getattr(lsp_tools, tool_class)
        assert hasattr(tool_cls, "description")
        assert tool_cls.description


class TestLspToolPrompts:
    """Tests that all LSP tools have proper tool prompts."""

    @pytest.mark.parametrize(
        "tool_class",
        [
            "LspDiagnosticsTool",
            "LspCompletionTool",
            "LspHoverTool",
            "LspDefinitionTool",
            "LspReferencesTool",
            "LspStatusTool",
        ],
    )
    def test_tool_prompt_is_cached(self, tool_class):
        tool_cls = getattr(lsp_tools, tool_class)
        prompt1 = tool_cls.get_tool_prompt()
        prompt2 = tool_cls.get_tool_prompt()
        assert prompt1 is prompt2


class TestLspDiagnosticsArgs:
    """Tests for LspDiagnosticsArgs model."""

    def test_validates_file_path_required(self):
        args = LspDiagnosticsArgs(file_path="/path/to/file.py")
        assert args.file_path == "/path/to/file.py"


class TestLspCompletionArgs:
    """Tests for LspCompletionArgs model."""

    def test_validates_required_fields(self):
        args = LspCompletionArgs(file_path="/path/to/file.py", line=10, col=5)
        assert args.file_path == "/path/to/file.py"
        assert args.line == 10
        assert args.col == 5


class TestLspHoverArgs:
    """Tests for LspHoverArgs model."""

    def test_validates_required_fields(self):
        args = LspHoverArgs(file_path="/path/to/file.py", line=10, col=5)
        assert args.file_path == "/path/to/file.py"
        assert args.line == 10
        assert args.col == 5


class TestLspReferencesArgs:
    """Tests for LspReferencesArgs model."""

    def test_include_declaration_default(self):
        args = LspReferencesArgs(file_path="/path/to/file.py", line=10, col=5)
        assert args.include_declaration is True

    def test_include_declaration_can_be_false(self):
        args = LspReferencesArgs(
            file_path="/path/to/file.py",
            line=10,
            col=5,
            include_declaration=False,
        )
        assert args.include_declaration is False


class TestLspWorkspaceSymbolsArgs:
    """Tests for LspWorkspaceSymbolsArgs model."""

    def test_query_required(self):
        args = LspWorkspaceSymbolsArgs(query="TestClass")
        assert args.query == "TestClass"


class TestLspRenameArgs:
    """Tests for LspRenameArgs model."""

    def test_new_name_required(self):
        args = LspRenameArgs(
            file_path="/path/to/file.py",
            line=10,
            col=5,
            new_name="NewName",
        )
        assert args.new_name == "NewName"