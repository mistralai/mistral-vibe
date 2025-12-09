from __future__ import annotations

from pathlib import Path

from acp import ReadTextFileRequest, ReadTextFileResponse, WriteTextFileRequest
import pytest

from vibe.acp.tools.builtins.search_replace import AcpSearchReplaceState, SearchReplace
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.search_replace import (
    SearchReplaceArgs,
    SearchReplaceConfig,
    SearchReplaceResult,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class MockConnection:
    def __init__(
        self,
        file_content: str = "original line 1\noriginal line 2\noriginal line 3",
        read_error: Exception | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self._file_content = file_content
        self._read_error = read_error
        self._write_error = write_error
        self._read_text_file_called = False
        self._write_text_file_called = False
        self._session_update_called = False
        self._last_read_request: ReadTextFileRequest | None = None
        self._last_write_request: WriteTextFileRequest | None = None
        self._write_calls: list[WriteTextFileRequest] = []

    async def readTextFile(self, request: ReadTextFileRequest) -> ReadTextFileResponse:
        self._read_text_file_called = True
        self._last_read_request = request

        if self._read_error:
            raise self._read_error

        return ReadTextFileResponse(content=self._file_content)

    async def writeTextFile(self, request: WriteTextFileRequest) -> None:
        self._write_text_file_called = True
        self._last_write_request = request
        self._write_calls.append(request)

        if self._write_error:
            raise self._write_error

    async def sessionUpdate(self, notification) -> None:
        self._session_update_called = True


@pytest.fixture
def mock_connection() -> MockConnection:
    return MockConnection()


@pytest.fixture
def acp_search_replace_tool(
    mock_connection: MockConnection, tmp_path: Path
) -> SearchReplace:
    config = SearchReplaceConfig(workdir=tmp_path)
    state = AcpSearchReplaceState.model_construct(
        connection=mock_connection,  # type: ignore[arg-type]
        session_id="test_session_123",
        tool_call_id="test_tool_call_456",
    )
    return SearchReplace(config=config, state=state)


class TestAcpSearchReplaceBasic:
    def test_get_name(self) -> None:
        assert SearchReplace.get_name() == "search_replace"


class TestAcpSearchReplaceExecution:
    @pytest.mark.asyncio
    async def test_run_success(
        self,
        acp_search_replace_tool: SearchReplace,
        mock_connection: MockConnection,
        tmp_path: Path,
    ) -> None:
        test_file = tmp_path / "test_file.txt"
        test_file.write_text("original line 1\noriginal line 2\noriginal line 3")
        search_replace_content = (
            "<<<<<<< SEARCH\noriginal line 2\n=======\nmodified line 2\n>>>>>>> REPLACE"
        )
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        result = await acp_search_replace_tool.run(args)

        assert isinstance(result, SearchReplaceResult)
        assert result.file == str(test_file)
        assert result.blocks_applied == 1
        assert mock_connection._read_text_file_called
        assert mock_connection._write_text_file_called
        assert mock_connection._session_update_called

        # Verify ReadTextFileRequest was created correctly
        read_request = mock_connection._last_read_request
        assert read_request is not None
        assert read_request.sessionId == "test_session_123"
        assert read_request.path == str(test_file)

        # Verify WriteTextFileRequest was created correctly
        write_request = mock_connection._last_write_request
        assert write_request is not None
        assert write_request.sessionId == "test_session_123"
        assert write_request.path == str(test_file)
        assert (
            write_request.content == "original line 1\nmodified line 2\noriginal line 3"
        )

    @pytest.mark.asyncio
    async def test_run_with_backup(
        self, mock_connection: MockConnection, tmp_path: Path
    ) -> None:
        config = SearchReplaceConfig(create_backup=True, workdir=tmp_path)
        tool = SearchReplace(
            config=config,
            state=AcpSearchReplaceState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        test_file = tmp_path / "test_file.txt"
        test_file.write_text("original line 1\noriginal line 2\noriginal line 3")
        search_replace_content = (
            "<<<<<<< SEARCH\noriginal line 1\n=======\nmodified line 1\n>>>>>>> REPLACE"
        )
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        result = await tool.run(args)

        assert result.blocks_applied == 1
        # Should have written the main file and the backup
        assert len(mock_connection._write_calls) >= 1
        # Check if backup was written (it should be written to .bak file)
        assert sum(w.path.endswith(".bak") for w in mock_connection._write_calls) == 1

    @pytest.mark.asyncio
    async def test_run_read_error(
        self, mock_connection: MockConnection, tmp_path: Path
    ) -> None:
        mock_connection._read_error = RuntimeError("File not found")

        tool = SearchReplace(
            config=SearchReplaceConfig(workdir=tmp_path),
            state=AcpSearchReplaceState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        test_file = tmp_path / "test.txt"
        test_file.touch()
        search_replace_content = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert (
            str(exc_info.value)
            == f"Unexpected error reading {test_file}: File not found"
        )

    @pytest.mark.asyncio
    async def test_run_write_error(
        self, mock_connection: MockConnection, tmp_path: Path
    ) -> None:
        mock_connection._write_error = RuntimeError("Permission denied")
        test_file = tmp_path / "test.txt"
        test_file.touch()
        mock_connection._file_content = "old"  # Update mock to return correct content

        tool = SearchReplace(
            config=SearchReplaceConfig(workdir=tmp_path),
            state=AcpSearchReplaceState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        search_replace_content = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert str(exc_info.value) == f"Error writing {test_file}: Permission denied"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "connection,session_id,expected_error",
        [
            (
                None,
                "test_session",
                "Connection not available in tool state. This tool can only be used within an ACP session.",
            ),
            (
                MockConnection(),
                None,
                "Session ID not available in tool state. This tool can only be used within an ACP session.",
            ),
        ],
    )
    async def test_run_without_required_state(
        self,
        tmp_path: Path,
        connection: MockConnection | None,
        session_id: str | None,
        expected_error: str,
    ) -> None:
        test_file = tmp_path / "test.txt"
        test_file.touch()
        tool = SearchReplace(
            config=SearchReplaceConfig(workdir=tmp_path),
            state=AcpSearchReplaceState.model_construct(
                connection=connection,  # type: ignore[arg-type]
                session_id=session_id,
                tool_call_id="test_call",
            ),
        )

        search_replace_content = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert str(exc_info.value) == expected_error


class TestAcpSearchReplaceSessionUpdates:
    def test_tool_call_session_update(self) -> None:
        search_replace_content = (
            "<<<<<<< SEARCH\nold text\n=======\nnew text\n>>>>>>> REPLACE"
        )
        event = ToolCallEvent(
            tool_name="search_replace",
            tool_call_id="test_call_123",
            args=SearchReplaceArgs(
                file_path="/tmp/test.txt", content=search_replace_content
            ),
            tool_class=SearchReplace,
        )

        update = SearchReplace.tool_call_session_update(event)
        assert update is not None
        assert update.sessionUpdate == "tool_call"
        assert update.toolCallId == "test_call_123"
        assert update.kind == "edit"
        assert update.title is not None
        assert update.content is not None
        assert len(update.content) == 1
        assert update.content[0].type == "diff"
        assert update.content[0].path == "/tmp/test.txt"
        assert update.content[0].oldText == "old text"
        assert update.content[0].newText == "new text"
        assert update.locations is not None
        assert len(update.locations) == 1
        assert update.locations[0].path == "/tmp/test.txt"

    def test_tool_call_session_update_invalid_args(self) -> None:
        class InvalidArgs:
            pass

        event = ToolCallEvent.model_construct(
            tool_name="search_replace",
            tool_call_id="test_call_123",
            args=InvalidArgs(),  # type: ignore[arg-type]
            tool_class=SearchReplace,
        )

        update = SearchReplace.tool_call_session_update(event)
        assert update is None

    def test_tool_result_session_update(self) -> None:
        search_replace_content = (
            "<<<<<<< SEARCH\nold text\n=======\nnew text\n>>>>>>> REPLACE"
        )
        result = SearchReplaceResult(
            file="/tmp/test.txt",
            blocks_applied=1,
            lines_changed=1,
            content=search_replace_content,
            warnings=[],
        )

        event = ToolResultEvent(
            tool_name="search_replace",
            tool_call_id="test_call_123",
            result=result,
            tool_class=SearchReplace,
        )

        update = SearchReplace.tool_result_session_update(event)
        assert update is not None
        assert update.sessionUpdate == "tool_call_update"
        assert update.toolCallId == "test_call_123"
        assert update.status == "completed"
        assert update.content is not None
        assert len(update.content) == 1
        assert update.content[0].type == "diff"
        assert update.content[0].path == "/tmp/test.txt"
        assert update.content[0].oldText == "old text"
        assert update.content[0].newText == "new text"
        assert update.locations is not None
        assert len(update.locations) == 1
        assert update.locations[0].path == "/tmp/test.txt"

    def test_tool_result_session_update_invalid_result(self) -> None:
        class InvalidResult:
            pass

        event = ToolResultEvent.model_construct(
            tool_name="search_replace",
            tool_call_id="test_call_123",
            result=InvalidResult(),  # type: ignore[arg-type]
            tool_class=SearchReplace,
        )

        update = SearchReplace.tool_result_session_update(event)
        assert update is None
