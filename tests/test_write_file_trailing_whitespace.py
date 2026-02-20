"""Test that write_file tool strips trailing whitespace."""

from pathlib import Path
from unittest.mock import patch

import pytest

from vibe.core.tools.builtins.write_file import (
    WriteFile, 
    WriteFileArgs, 
    WriteFileConfig, 
    WriteFileState
)


@pytest.fixture
def write_file_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WriteFile:
    """Create a WriteFile tool instance with default config and state."""
    # Change working directory to tmp_path to allow writing files there
    monkeypatch.chdir(tmp_path)
    config = WriteFileConfig()
    state = WriteFileState()
    return WriteFile(config=config, state=state)


class TestWriteFileTrailingWhitespace:
    @pytest.mark.asyncio
    async def test_strips_trailing_whitespace(self, tmp_path: Path, write_file_tool: WriteFile) -> None:
        """Test that write_file automatically strips trailing whitespace."""
        test_file = tmp_path / "test.txt"
        
        # Content with trailing whitespace
        content_with_whitespace = "line1  \nline2  \nline3"
        args = WriteFileArgs(path=str(test_file), content=content_with_whitespace, overwrite=True)
        
        result = await write_file_tool.run(args).__anext__()
        
        # Read the written file
        written_content = test_file.read_text()
        
        # Verify trailing whitespace was stripped
        assert written_content == "line1\nline2\nline3"
        assert "  \n" not in written_content
        assert "  " not in written_content  # No trailing spaces at end of lines
        
    @pytest.mark.asyncio
    async def test_preserves_line_structure(self, tmp_path: Path, write_file_tool: WriteFile) -> None:
        """Test that write_file preserves line structure while stripping trailing whitespace."""
        test_file = tmp_path / "test.txt"
        
        # Content with mixed whitespace and empty lines
        content = "header  \n  \ncontent  \n  \nfooter"
        args = WriteFileArgs(path=str(test_file), content=content, overwrite=True)
        
        result = await write_file_tool.run(args).__anext__()
        
        # Read the written file
        written_content = test_file.read_text()
        
        # Verify structure is preserved but trailing whitespace stripped
        lines = written_content.split('\n')
        assert lines == ['header', '', 'content', '', 'footer']
        assert all(line == line.rstrip() for line in lines)  # No line has trailing whitespace
        
    @pytest.mark.asyncio
    async def test_strips_whitespace_method(self, write_file_tool: WriteFile) -> None:
        """Test the _strip_trailing_whitespace method directly."""
        # Test various whitespace patterns
        test_cases = [
            ("hello  \nworld  ", "hello\nworld"),
            ("line1  \n  \nline2  ", "line1\n\nline2"),
            ("no_whitespace", "no_whitespace"),
            ("  leading_and_trailing  ", "  leading_and_trailing"),  # Only trailing stripped
            ("", ""),
            ("\n", "\n"),  # Single newline preserved
        ]
        
        for input_text, expected in test_cases:
            result = write_file_tool._strip_trailing_whitespace(input_text)
            assert result == expected, f"Failed for input: {input_text!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])