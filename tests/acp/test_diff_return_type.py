from __future__ import annotations

import pytest
from acp.schema import (
    FileEditToolCallContent,
    ToolCallProgress,
    ToolCallStart,
)
from vibe.acp.tools.builtins.search_replace import SearchReplace
from vibe.acp.tools.builtins.write_file import WriteFile
from vibe.core.types import ToolCallEvent, ToolResultEvent


def _make_tool_call_event(tool_class, args=None, tool_call_id="tc-1"):
    return ToolCallEvent(
        tool_name=tool_class.get_name(),
        tool_class=tool_class,
        tool_call_id=tool_call_id,
        args=args,
    )


def _make_tool_result_event(tool_class, result=None, error=None, tool_call_id="tc-1"):
    return ToolResultEvent(
        tool_name=tool_class.get_name(),
        tool_class=tool_class,
        tool_call_id=tool_call_id,
        result=result,
        error=error,
    )


class TestSearchReplaceDiffType:
    """Verify search_replace tool returns correct diff types via ACP."""

    @pytest.mark.asyncio
    async def test_tool_call_uses_edit_kind_and_diff_type(self) -> None:
        from vibe.core.tools.builtins.search_replace import SearchReplaceArgs

        args = SearchReplaceArgs(
            file_path="/tmp/test.py",
            content="<<< SEARCH\nold\n=== REPLACE\nnew",
        )
        event = _make_tool_call_event(SearchReplace, args=args)
        update = SearchReplace.tool_call_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallStart)
        assert update.kind == "edit"
        assert update.content is not None
        for c in update.content:
            assert isinstance(c, FileEditToolCallContent)
            assert c.type == "diff"

    @pytest.mark.asyncio
    async def test_tool_result_uses_edit_kind_and_diff_type(self) -> None:
        from vibe.core.tools.builtins.search_replace import SearchReplaceResult

        result = SearchReplaceResult(
            file="/tmp/test.py",
            blocks_applied=1,
            lines_changed=1,
            content="<<< SEARCH\nold\n=== REPLACE\nnew",
            warnings=[],
        )
        event = _make_tool_result_event(SearchReplace, result=result)
        update = SearchReplace.tool_result_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.kind == "edit"
        assert update.content is not None
        for c in update.content:
            assert isinstance(c, FileEditToolCallContent)
            assert c.type == "diff"

    @pytest.mark.asyncio
    async def test_tool_result_failure_has_edit_kind(self) -> None:
        event = _make_tool_result_event(
            SearchReplace, error="something went wrong"
        )
        update = SearchReplace.tool_result_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.status == "failed"
        assert update.kind == "edit"


class TestWriteFileDiffType:
    """Verify write_file tool returns correct diff types via ACP."""

    @pytest.mark.asyncio
    async def test_tool_call_uses_edit_kind_and_diff_type(self) -> None:
        from vibe.core.tools.builtins.write_file import WriteFileArgs

        args = WriteFileArgs(path="/tmp/test.py", content="hello", overwrite=True)
        event = _make_tool_call_event(WriteFile, args=args)
        update = WriteFile.tool_call_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallStart)
        assert update.kind == "edit"
        assert update.content is not None
        for c in update.content:
            assert isinstance(c, FileEditToolCallContent)
            assert c.type == "diff"

    @pytest.mark.asyncio
    async def test_tool_result_uses_edit_kind_and_diff_type(self) -> None:
        from vibe.core.tools.builtins.write_file import WriteFileResult

        result = WriteFileResult(
            path="/tmp/test.py",
            bytes_written=5,
            file_existed=False,
            content="hello",
        )
        event = _make_tool_result_event(WriteFile, result=result)
        update = WriteFile.tool_result_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.kind == "edit"
        assert update.content is not None
        for c in update.content:
            assert isinstance(c, FileEditToolCallContent)
            assert c.type == "diff"

    @pytest.mark.asyncio
    async def test_tool_result_failure_has_edit_kind(self) -> None:
        event = _make_tool_result_event(
            WriteFile, error="something went wrong"
        )
        update = WriteFile.tool_result_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.status == "failed"
        assert update.kind == "edit"


class TestGenericToolResultKind:
    """Verify generic tool result path sets kind correctly."""

    @pytest.mark.asyncio
    async def test_generic_edit_tool_gets_edit_kind_write_file(self) -> None:
        from vibe.acp.tools.session_update import tool_result_session_update
        from vibe.core.tools.builtins.write_file import WriteFileArgs, WriteFileResult

        args = WriteFileArgs(path="/tmp/test.py", content="hello", overwrite=True)
        result = WriteFileResult(
            path="/tmp/test.py",
            bytes_written=5,
            file_existed=False,
            content="hello",
        )

        event = ToolResultEvent(
            tool_name="write_file",
            tool_class=None,
            tool_call_id="tc-1",
            args=args,
            result=result,
        )

        update = tool_result_session_update(event)
        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.kind == "edit"

    @pytest.mark.asyncio
    async def test_generic_edit_tool_gets_edit_kind_search_replace(self) -> None:
        from vibe.acp.tools.session_update import tool_result_session_update
        from vibe.core.tools.builtins.search_replace import (
            SearchReplaceArgs,
            SearchReplaceResult,
        )

        args = SearchReplaceArgs(
            file_path="/tmp/test.py",
            content="<<< SEARCH\nold\n=== REPLACE\nnew",
        )
        result = SearchReplaceResult(
            file="/tmp/test.py",
            blocks_applied=1,
            lines_changed=1,
            content="<<< SEARCH\nold\n=== REPLACE\nnew",
            warnings=[],
        )

        event = ToolResultEvent(
            tool_name="search_replace",
            tool_class=None,
            tool_call_id="tc-1",
            args=args,
            result=result,
        )

        update = tool_result_session_update(event)
        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.kind == "edit"

    @pytest.mark.asyncio
    async def test_generic_grep_tool_gets_search_kind(self) -> None:
        from vibe.acp.tools.session_update import tool_result_session_update

        event = ToolResultEvent(
            tool_name="grep",
            tool_class=None,
            tool_call_id="tc-1",
            args=None,
            result=None,
        )

        update = tool_result_session_update(event)
        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.kind == "search"

    @pytest.mark.asyncio
    async def test_generic_other_tool_gets_other_kind(self) -> None:
        from vibe.acp.tools.session_update import tool_result_session_update

        event = ToolResultEvent(
            tool_name="unknown_tool",
            tool_class=None,
            tool_call_id="tc-1",
            args=None,
            result=None,
        )

        update = tool_result_session_update(event)
        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.kind == "other"
