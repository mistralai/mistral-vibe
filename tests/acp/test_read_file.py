from __future__ import annotations

from pathlib import Path

from acp import ReadTextFileResponse
import pytest

from tests.mock.utils import collect_result
from vibe.acp.tools.builtins.read_file import AcpReadFileState, ReadFile
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.read_file import (
    ReadFileArgs,
    ReadFileResult,
    ReadFileToolConfig,
)


class MockClient:
    def __init__(
        self,
        file_content: str = "line 1\nline 2\nline 3",
        read_error: Exception | None = None,
    ) -> None:
        self._file_content = file_content
        self._read_error = read_error
        self._read_text_file_called = False
        self._session_update_called = False
        self._last_read_params: dict[str, str | int | None] = {}

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

        content = self._file_content
        if line is not None or limit is not None:
            lines = content.splitlines(keepends=True)
            start_line = (line or 1) - 1  # Convert to 0-indexed
            end_line = start_line + limit if limit is not None else len(lines)
            lines = lines[start_line:end_line]
            content = "".join(lines)

        return ReadTextFileResponse(content=content)

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        self._session_update_called = True


@pytest.fixture
def mock_client() -> MockClient:
    return MockClient()


@pytest.fixture
def acp_read_file_tool(
    mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ReadFile:
    monkeypatch.chdir(tmp_path)
    config = ReadFileToolConfig()
    state = AcpReadFileState.model_construct(
        client=mock_client,  # type: ignore[arg-type]
        session_id="test_session_123",
        tool_call_id="test_tool_call_456",
    )
    return ReadFile(config=config, state=state)


class TestAcpReadFileBasic:
    def test_get_name(self) -> None:
        assert ReadFile.get_name() == "read_file"


class TestAcpReadFileExecution:
    @pytest.mark.asyncio
    async def test_run_success(
        self, acp_read_file_tool: ReadFile, mock_client: MockClient, tmp_path: Path
    ) -> None:
        test_file = tmp_path / "test_file.txt"
        test_file.touch()
        args = ReadFileArgs(path=str(test_file))
        result = await collect_result(acp_read_file_tool.run(args))

        assert isinstance(result, ReadFileResult)
        assert result.path == str(test_file)
        assert result.content == "line 1\nline 2\nline 3"
        assert result.lines_read == 3
        assert mock_client._read_text_file_called
        assert mock_client._session_update_called

        # Verify read_text_file was called correctly
        params = mock_client._last_read_params
        assert params["session_id"] == "test_session_123"
        assert params["path"] == str(test_file)
        assert params["line"] is None  # offset=0 means no line specified
        assert params["limit"] is None

    @pytest.mark.asyncio
    async def test_run_with_offset(
        self, mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test_file.txt"
        test_file.touch()
        tool = ReadFile(
            config=ReadFileToolConfig(),
            state=AcpReadFileState.model_construct(
                client=mock_client, session_id="test_session", tool_call_id="test_call"
            ),
        )

        args = ReadFileArgs(path=str(test_file), offset=1)
        result = await collect_result(tool.run(args))

        assert result.lines_read == 2
        assert result.content == "line 2\nline 3"

        params = mock_client._last_read_params
        assert params["line"] == 2  # offset=1 means line 2 (1-indexed)

    @pytest.mark.asyncio
    async def test_run_with_limit(
        self, mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test_file.txt"
        test_file.touch()
        tool = ReadFile(
            config=ReadFileToolConfig(),
            state=AcpReadFileState.model_construct(
                client=mock_client, session_id="test_session", tool_call_id="test_call"
            ),
        )

        args = ReadFileArgs(path=str(test_file), limit=2)
        result = await collect_result(tool.run(args))

        assert result.lines_read == 2
        assert result.content == "line 1\nline 2\n"

        params = mock_client._last_read_params
        assert params["limit"] == 2

    @pytest.mark.asyncio
    async def test_run_with_offset_and_limit(
        self, mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test_file.txt"
        test_file.touch()
        tool = ReadFile(
            config=ReadFileToolConfig(),
            state=AcpReadFileState.model_construct(
                client=mock_client, session_id="test_session", tool_call_id="test_call"
            ),
        )

        args = ReadFileArgs(path=str(test_file), offset=1, limit=1)
        result = await collect_result(tool.run(args))

        assert result.lines_read == 1
        assert result.content == "line 2\n"

        params = mock_client._last_read_params
        assert params["line"] == 2
        assert params["limit"] == 1

    @pytest.mark.asyncio
    async def test_run_read_error(
        self, mock_client: MockClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mock_client._read_error = RuntimeError("File not found")
        test_file = tmp_path / "test.txt"
        test_file.touch()
        tool = ReadFile(
            config=ReadFileToolConfig(),
            state=AcpReadFileState.model_construct(
                client=mock_client, session_id="test_session", tool_call_id="test_call"
            ),
        )

        args = ReadFileArgs(path=str(test_file))
        with pytest.raises(ToolError) as exc_info:
            await collect_result(tool.run(args))

        assert str(exc_info.value) == f"Error reading {test_file}: File not found"

    @pytest.mark.asyncio
    async def test_run_without_connection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.touch()
        tool = ReadFile(
            config=ReadFileToolConfig(),
            state=AcpReadFileState.model_construct(
                client=None, session_id="test_session", tool_call_id="test_call"
            ),
        )

        args = ReadFileArgs(path=str(test_file))
        with pytest.raises(ToolError) as exc_info:
            await collect_result(tool.run(args))

        assert (
            str(exc_info.value)
            == "Client not available in tool state. This tool can only be used within an ACP session."
        )

    @pytest.mark.asyncio
    async def test_run_without_session_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.touch()
        mock_client = MockClient()
        tool = ReadFile(
            config=ReadFileToolConfig(),
            state=AcpReadFileState.model_construct(
                client=mock_client, session_id=None, tool_call_id="test_call"
            ),
        )

        args = ReadFileArgs(path=str(test_file))
        with pytest.raises(ToolError) as exc_info:
            await collect_result(tool.run(args))

        assert (
            str(exc_info.value)
            == "Session ID not available in tool state. This tool can only be used within an ACP session."
        )
