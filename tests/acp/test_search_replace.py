from __future__ import annotations

from pathlib import Path

from acp import ReadTextFileResponse
import pytest

from tests.mock.utils import collect_result
from vibe.acp.tools.builtins.search_replace import AcpSearchReplaceState, SearchReplace
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.search_replace import (
    SearchReplaceArgs,
    SearchReplaceConfig,
    SearchReplaceResult,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class MockClient:
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
        self._last_read_params: dict[str, str | int | None] = {}
        self._last_write_params: dict[str, str] = {}
        self._write_calls: list[dict[str, str]] = []

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs,
    ) -> ReadTextFileResponse:
        self._read_text_file_called = True
        self._last_read_params = {
            "path": path,
            "session_id": session_id,
            "limit": limit,
            "line": line,
        }

        if self._read_error:
            raise self._read_error

        return ReadTextFileResponse(content=self._file_content)

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs
    ) -> None:
        self._write_text_file_called = True
        params = {"content": content, "path": path, "session_id": session_id}
        self._last_write_params = params
        self._write_calls.append(params)

        if self._write_error:
            raise self._write_error

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        self._session_update_called = True


@pytest.fixture
def mock_client() -> MockClient:
    return MockClient()


@pytest.fixture
def acp_search_replace_tool(
    mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SearchReplace:
    monkeypatch.chdir(tmp_path)
    config = SearchReplaceConfig()
    state = AcpSearchReplaceState.model_construct(
        client=mock_client,
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
        mock_client: MockClient,
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
        result = await collect_result(acp_search_replace_tool.run(args))

        assert isinstance(result, SearchReplaceResult)
        assert result.file == str(test_file)
        assert result.blocks_applied == 1
        assert mock_client._read_text_file_called
        assert mock_client._write_text_file_called
        assert mock_client._session_update_called

        # Verify read_text_file was called correctly
        read_params = mock_client._last_read_params
        assert read_params["session_id"] == "test_session_123"
        assert read_params["path"] == str(test_file)

        # Verify write_text_file was called correctly
        write_params = mock_client._last_write_params
        assert write_params["session_id"] == "test_session_123"
        assert write_params["path"] == str(test_file)
        assert (
            write_params["content"]
            == "original line 1\nmodified line 2\noriginal line 3"
        )

    @pytest.mark.asyncio
    async def test_run_with_backup(
        self, mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config = SearchReplaceConfig(create_backup=True)
        tool = SearchReplace(
            config=config,
            state=AcpSearchReplaceState.model_construct(
                client=mock_client, session_id="test_session", tool_call_id="test_call"
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
        result = await collect_result(tool.run(args))

        assert result.blocks_applied == 1
        # Should have written the main file and the backup
        assert len(mock_client._write_calls) >= 1
        # Check if backup was written (it should be written to .bak file)
        assert sum(w["path"].endswith(".bak") for w in mock_client._write_calls) == 1

    @pytest.mark.asyncio
    async def test_run_read_error(
        self, mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mock_client._read_error = RuntimeError("File not found")

        tool = SearchReplace(
            config=SearchReplaceConfig(),
            state=AcpSearchReplaceState.model_construct(
                client=mock_client, session_id="test_session", tool_call_id="test_call"
            ),
        )

        test_file = tmp_path / "test.txt"
        test_file.touch()
        search_replace_content = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        with pytest.raises(ToolError) as exc_info:
            await collect_result(tool.run(args))

        assert (
            str(exc_info.value)
            == f"Unexpected error reading {test_file}: File not found"
        )

    @pytest.mark.asyncio
    async def test_run_write_error(
        self, mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mock_client._write_error = RuntimeError("Permission denied")
        test_file = tmp_path / "test.txt"
        test_file.touch()
        mock_client._file_content = "old"  # Update mock to return correct content

        tool = SearchReplace(
            config=SearchReplaceConfig(),
            state=AcpSearchReplaceState.model_construct(
                client=mock_client, session_id="test_session", tool_call_id="test_call"
            ),
        )

        search_replace_content = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        with pytest.raises(ToolError) as exc_info:
            await collect_result(tool.run(args))

        assert str(exc_info.value) == f"Error writing {test_file}: Permission denied"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "client,session_id,expected_error",
        [
            (
                None,
                "test_session",
                "Client not available in tool state. This tool can only be used within an ACP session.",
            ),
            (
                MockClient(),
                None,
                "Session ID not available in tool state. This tool can only be used within an ACP session.",
            ),
        ],
    )
    async def test_run_without_required_state(
        self,
        tmp_path: Path,
        client: MockClient | None,
        session_id: str | None,
        expected_error: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.touch()
        tool = SearchReplace(
            config=SearchReplaceConfig(),
            state=AcpSearchReplaceState.model_construct(
                client=client, session_id=session_id, tool_call_id="test_call"
            ),
        )

        search_replace_content = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        args = SearchReplaceArgs(
            file_path=str(test_file), content=search_replace_content
        )
        with pytest.raises(ToolError) as exc_info:
            await collect_result(tool.run(args))

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
        assert update.session_update == "tool_call"
        assert update.tool_call_id == "test_call_123"
        assert update.kind == "edit"
        assert update.title is not None
        assert update.content is not None
        assert isinstance(update.content, list)
        assert len(update.content) == 1
        assert update.content[0].type == "diff"
        assert update.content[0].path == "/tmp/test.txt"
        assert update.content[0].old_text == "old text"
        assert update.content[0].new_text == "new text"
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
        assert update.session_update == "tool_call_update"
        assert update.tool_call_id == "test_call_123"
        assert update.status == "completed"
        assert update.content is not None
        assert isinstance(update.content, list)
        assert len(update.content) == 1
        assert update.content[0].type == "diff"
        assert update.content[0].path == "/tmp/test.txt"
        assert update.content[0].old_text == "old text"
        assert update.content[0].new_text == "new text"
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
