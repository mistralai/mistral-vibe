from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.ast_grep import (
    AstGrep,
    AstGrepArgs,
    AstGrepResult,
    AstGrepToolConfig,
)


@pytest.fixture
def ast_grep_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = AstGrepToolConfig()
    return AstGrep(config=config, state=BaseToolState())


@pytest.mark.asyncio
async def test_ast_grep_search(ast_grep_tool: AstGrep, tmp_path) -> None:
    """Test basic AST pattern search."""
    # Create a test Rust file
    test_file = tmp_path / "test.rs"
    test_file.write_text("""
fn add(a: i32, b: i32) -> i32 {
    a + b
}

fn subtract(a: i32, b: i32) -> i32 {
    a - b
}
""")

    args = AstGrepArgs(
        pattern="fn add",
        path=str(test_file),
        lang="rust"
    )

    # Mock Python API method
    with patch.object(ast_grep_tool, '_run_with_python_api', new_callable=AsyncMock) as mock_python:

        # Set up mock to return expected result
        expected_result = AstGrepResult(
            matches="Match 1: fn add(a: i32, b: i32) -> i32 { a + b } (lines 2-4)",
            match_count=1,
            was_truncated=False,
            rewritten=False
        )
        mock_python.return_value = expected_result

        result = []
        async for event in ast_grep_tool.run(args):
            result.append(event)

        assert len(result) == 1
        assert isinstance(result[0], AstGrepResult)
        assert result[0].matches == "Match 1: fn add(a: i32, b: i32) -> i32 { a + b } (lines 2-4)"
        assert result[0].match_count == 1
        assert result[0].rewritten is False


@pytest.mark.asyncio
async def test_ast_grep_rewrite(ast_grep_tool: AstGrep, tmp_path) -> None:
    """Test AST pattern rewrite."""
    # Create a test Rust file
    test_file = tmp_path / "test.rs"
    test_file.write_text("""
fn add(a: i32, b: i32) -> i32 {
    a + b
}
""")

    args = AstGrepArgs(
        pattern="fn add",
        rewrite="fn sum",
        path=str(test_file),
        lang="rust"
    )

    # Mock Python API method
    with patch.object(ast_grep_tool, '_run_with_python_api', new_callable=AsyncMock) as mock_python:

        # Set up mock to return expected result
        expected_result = AstGrepResult(
            matches="fn sum(a: i32, b: i32) -> i32 { a + b }",
            match_count=1,
            was_truncated=False,
            rewritten=True
        )
        mock_python.return_value = expected_result

        result = []
        async for event in ast_grep_tool.run(args):
            result.append(event)

        assert len(result) == 1
        assert isinstance(result[0], AstGrepResult)
        assert result[0].rewritten is True
        assert result[0].match_count == 1


@pytest.mark.asyncio
async def test_ast_grep_empty_pattern(ast_grep_tool: AstGrep, tmp_path) -> None:
    """Test empty pattern validation."""
    args = AstGrepArgs(
        pattern="",
        path=str(tmp_path / "test.rs")
    )

    with pytest.raises(ToolError, match="Empty search pattern"):
        async for _ in ast_grep_tool.run(args):
            pass


@pytest.mark.asyncio
async def test_ast_grep_nonexistent_path(ast_grep_tool: AstGrep) -> None:
    """Test nonexistent path validation."""
    args = AstGrepArgs(
        pattern="fn test",
        path="/nonexistent/path.rs"
    )

    with pytest.raises(ToolError, match="Path does not exist"):
        async for _ in ast_grep_tool.run(args):
            pass


@pytest.mark.asyncio
async def test_ast_grep_missing_lang(ast_grep_tool: AstGrep, tmp_path) -> None:
    """Test missing language validation."""
    test_file = tmp_path / "test.rs"
    test_file.write_text("fn test() {}")

    args = AstGrepArgs(
        pattern="fn test",
        path=str(test_file)
        # lang is None
    )

    with pytest.raises(ToolError, match="Language must be specified"):
        async for _ in ast_grep_tool.run(args):
            pass


@pytest.mark.asyncio
async def test_ast_grep_format_call_display(ast_grep_tool: AstGrep) -> None:
    """Test call display formatting."""
    args = AstGrepArgs(
        pattern="fn add($$) -> $$ { $$ }",
        rewrite="fn sum($1) -> $2 { $3 }",
        path="src/main.rs",
        lang="rust"
    )

    display = ast_grep_tool.format_call_display(args)
    assert "AST search 'fn add($$) -> $$ { $$ }'" in display.summary
    assert "src/main.rs" in display.summary
    assert "rust" in display.summary
    assert "fn sum($1) -> $2 { $3 }" in display.summary


def test_ast_grep_get_result_display_success(ast_grep_tool: AstGrep) -> None:
    """Test successful result display."""
    result = AstGrepResult(
        matches="fn add(a: i32, b: i32) -> i32 { a + b }",
        match_count=1,
        was_truncated=False,
        rewritten=False
    )

    mock_event = MagicMock()
    mock_event.result = result

    display = ast_grep_tool.get_result_display(mock_event)
    assert display.success is True
    assert "Found 1 matches" in display.message


def test_ast_grep_get_result_display_rewrite(ast_grep_tool: AstGrep) -> None:
    """Test rewrite result display."""
    result = AstGrepResult(
        matches="fn sum(a: i32, b: i32) -> i32 { a + b }",
        match_count=2,
        was_truncated=True,
        rewritten=True
    )

    mock_event = MagicMock()
    mock_event.result = result

    display = ast_grep_tool.get_result_display(mock_event)
    assert display.success is True
    assert "Rewrote 2 matches" in display.message
    assert "(truncated)" in display.message
    assert len(display.warnings) == 1


def test_ast_grep_get_status_text(ast_grep_tool: AstGrep) -> None:
    """Test status text."""
    status = ast_grep_tool.get_status_text()
    assert status == "Analyzing code structure"
