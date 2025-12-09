from __future__ import annotations

import asyncio

from acp.schema import TerminalOutputResponse, WaitForTerminalExitResponse
import pytest

from vibe.acp.tools.builtins.bash import AcpBashState, Bash
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.bash import BashArgs, BashResult, BashToolConfig


class MockTerminalHandle:
    def __init__(
        self,
        terminal_id: str = "test_terminal_123",
        exit_code: int | None = 0,
        output: str = "test output",
        wait_delay: float = 0.01,
    ) -> None:
        self.id = terminal_id
        self._exit_code = exit_code
        self._output = output
        self._wait_delay = wait_delay
        self._killed = False

    async def wait_for_exit(self) -> WaitForTerminalExitResponse:
        await asyncio.sleep(self._wait_delay)
        return WaitForTerminalExitResponse(exitCode=self._exit_code)

    async def current_output(self) -> TerminalOutputResponse:
        return TerminalOutputResponse(output=self._output, truncated=False)

    async def kill(self) -> None:
        self._killed = True

    async def release(self) -> None:
        pass


class MockConnection:
    def __init__(self, terminal_handle: MockTerminalHandle | None = None) -> None:
        self._terminal_handle = terminal_handle or MockTerminalHandle()
        self._create_terminal_called = False
        self._session_update_called = False
        self._create_terminal_error: Exception | None = None
        self._last_create_request = None

    async def createTerminal(self, request) -> MockTerminalHandle:
        self._create_terminal_called = True
        self._last_create_request = request
        if self._create_terminal_error:
            raise self._create_terminal_error
        return self._terminal_handle

    async def sessionUpdate(self, notification) -> None:
        self._session_update_called = True


@pytest.fixture
def mock_connection() -> MockConnection:
    return MockConnection()


@pytest.fixture
def acp_bash_tool(mock_connection: MockConnection) -> Bash:
    config = BashToolConfig()
    # Use model_construct to bypass Pydantic validation for testing
    state = AcpBashState.model_construct(
        connection=mock_connection,  # type: ignore[arg-type]
        session_id="test_session_123",
        tool_call_id="test_tool_call_456",
    )
    return Bash(config=config, state=state)


class TestAcpBashBasic:
    def test_get_name(self) -> None:
        assert Bash.get_name() == "bash"

    def test_get_summary_simple_command(self) -> None:
        args = BashArgs(command="ls")
        display = Bash.get_summary(args)
        assert display == "ls"

    def test_get_summary_with_timeout(self) -> None:
        args = BashArgs(command="ls", timeout=10)
        display = Bash.get_summary(args)
        assert display == "ls (timeout 10s)"

    def test_parse_command_simple(self) -> None:
        tool = Bash(config=BashToolConfig(), state=AcpBashState())
        env, command, args = tool._parse_command("ls")
        assert env == []
        assert command == "ls"
        assert args == []

    def test_parse_command_with_args(self) -> None:
        tool = Bash(config=BashToolConfig(), state=AcpBashState())
        env, command, args = tool._parse_command("ls -la src")
        assert env == []
        assert command == "ls"
        assert args == ["-la", "src"]

    def test_parse_command_with_env(self) -> None:
        tool = Bash(config=BashToolConfig(), state=AcpBashState())
        env, command, args = tool._parse_command("NODE_ENV=test DEBUG=1 npm test")
        assert len(env) == 2
        assert env[0].name == "NODE_ENV"
        assert env[0].value == "test"
        assert env[1].name == "DEBUG"
        assert env[1].value == "1"
        assert command == "npm"
        assert args == ["test"]

    def test_parse_command_with_env_value_contains_equals(self) -> None:
        tool = Bash(config=BashToolConfig(), state=AcpBashState())
        env, command, args = tool._parse_command(
            "PATH=/usr/bin:/usr/local/bin echo hello"
        )
        assert len(env) == 1
        assert env[0].name == "PATH"
        assert env[0].value == "/usr/bin:/usr/local/bin"
        assert command == "echo"
        assert args == ["hello"]


class TestAcpBashExecution:
    @pytest.mark.asyncio
    async def test_run_success(
        self, acp_bash_tool: Bash, mock_connection: MockConnection
    ) -> None:
        from pathlib import Path

        args = BashArgs(command="echo hello")
        result = await acp_bash_tool.run(args)

        assert isinstance(result, BashResult)
        assert result.stdout == "test output"
        assert result.stderr == ""
        assert result.returncode == 0
        assert mock_connection._create_terminal_called

        # Verify CreateTerminalRequest was created correctly
        request = mock_connection._last_create_request
        assert request is not None
        assert request.sessionId == "test_session_123"
        assert request.command == "echo"
        assert request.args == ["hello"]
        assert request.cwd == str(Path.cwd())  # effective_workdir defaults to cwd

    @pytest.mark.asyncio
    async def test_run_creates_terminal_with_env_vars(
        self, mock_connection: MockConnection
    ) -> None:
        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="NODE_ENV=test npm run build")
        await tool.run(args)

        request = mock_connection._last_create_request
        assert request is not None
        assert len(request.env) == 1
        assert request.env[0].name == "NODE_ENV"
        assert request.env[0].value == "test"
        assert request.command == "npm"
        assert request.args == ["run", "build"]

    @pytest.mark.asyncio
    async def test_run_with_nonzero_exit_code(
        self, mock_connection: MockConnection
    ) -> None:
        custom_handle = MockTerminalHandle(
            terminal_id="custom_terminal", exit_code=1, output="error: command failed"
        )
        mock_connection._terminal_handle = custom_handle

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test_command")
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert (
            str(exc_info.value)
            == "Command failed: 'test_command'\nReturn code: 1\nStdout: error: command failed"
        )

    @pytest.mark.asyncio
    async def test_run_create_terminal_failure(
        self, mock_connection: MockConnection
    ) -> None:
        mock_connection._create_terminal_error = RuntimeError("Connection failed")

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test")
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert (
            str(exc_info.value)
            == "Failed to create terminal: RuntimeError('Connection failed')"
        )

    @pytest.mark.asyncio
    async def test_run_without_connection(self) -> None:
        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=None, session_id="test_session", tool_call_id="test_call"
            ),
        )

        args = BashArgs(command="test")
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert (
            str(exc_info.value)
            == "Connection not available in tool state. This tool can only be used within an ACP session."
        )

    @pytest.mark.asyncio
    async def test_run_without_session_id(self) -> None:
        mock_connection = MockConnection()
        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id=None,
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test")
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert (
            str(exc_info.value)
            == "Session ID not available in tool state. This tool can only be used within an ACP session."
        )

    @pytest.mark.asyncio
    async def test_run_with_none_exit_code(
        self, mock_connection: MockConnection
    ) -> None:
        custom_handle = MockTerminalHandle(
            terminal_id="none_exit_terminal", exit_code=None, output="output"
        )
        mock_connection._terminal_handle = custom_handle

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test_command")
        result = await tool.run(args)

        assert result.returncode == 0
        assert result.stdout == "output"


class TestAcpBashTimeout:
    @pytest.mark.asyncio
    async def test_run_with_timeout_raises_error_and_kills(
        self, mock_connection: MockConnection
    ) -> None:
        custom_handle = MockTerminalHandle(
            terminal_id="timeout_terminal",
            output="partial output",
            wait_delay=20,  # Longer than the 1 second timeout
        )
        mock_connection._terminal_handle = custom_handle

        # Use a config with different default timeout to verify args timeout overrides it
        tool = Bash(
            config=BashToolConfig(default_timeout=30),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="slow_command", timeout=1)
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert str(exc_info.value) == "Command timed out after 1s: 'slow_command'"
        assert custom_handle._killed

    @pytest.mark.asyncio
    async def test_run_timeout_handles_kill_failure(
        self, mock_connection: MockConnection
    ) -> None:
        custom_handle = MockTerminalHandle(
            terminal_id="kill_failure_terminal",
            wait_delay=20,  # Longer than the 1 second timeout
        )
        mock_connection._terminal_handle = custom_handle

        async def failing_kill() -> None:
            raise RuntimeError("Kill failed")

        custom_handle.kill = failing_kill

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="slow_command", timeout=1)
        # Should still raise timeout error even if kill fails
        with pytest.raises(ToolError) as exc_info:
            await tool.run(args)

        assert str(exc_info.value) == "Command timed out after 1s: 'slow_command'"


class TestAcpBashEmbedding:
    @pytest.mark.asyncio
    async def test_run_with_embedding(self, mock_connection: MockConnection) -> None:
        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test")
        await tool.run(args)

        assert mock_connection._session_update_called

    @pytest.mark.asyncio
    async def test_run_embedding_without_tool_call_id(
        self, mock_connection: MockConnection
    ) -> None:
        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id=None,
            ),
        )

        args = BashArgs(command="test")
        await tool.run(args)

        # Embedding should be skipped when tool_call_id is None
        assert not mock_connection._session_update_called

    @pytest.mark.asyncio
    async def test_run_embedding_handles_exception(
        self, mock_connection: MockConnection
    ) -> None:
        # Make sessionUpdate raise an exception
        async def failing_session_update(notification) -> None:
            raise RuntimeError("Session update failed")

        mock_connection.sessionUpdate = failing_session_update

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test")
        # Should not raise, embedding failure is silently ignored
        result = await tool.run(args)

        assert result is not None
        assert result.stdout == "test output"


class TestAcpBashConfig:
    @pytest.mark.asyncio
    async def test_run_uses_config_default_timeout(
        self, mock_connection: MockConnection
    ) -> None:
        custom_handle = MockTerminalHandle(
            terminal_id="config_timeout_terminal",
            wait_delay=0.01,  # Shorter than config timeout
        )
        mock_connection._terminal_handle = custom_handle

        tool = Bash(
            config=BashToolConfig(default_timeout=30),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="fast", timeout=None)
        result = await tool.run(args)

        # Should succeed with config timeout
        assert result.returncode == 0


class TestAcpBashCleanup:
    @pytest.mark.asyncio
    async def test_run_releases_terminal_on_success(
        self, mock_connection: MockConnection
    ) -> None:
        custom_handle = MockTerminalHandle(terminal_id="cleanup_terminal")
        mock_connection._terminal_handle = custom_handle

        release_called = False

        async def mock_release() -> None:
            nonlocal release_called
            release_called = True

        custom_handle.release = mock_release

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test")
        await tool.run(args)

        assert release_called

    @pytest.mark.asyncio
    async def test_run_releases_terminal_on_timeout(
        self, mock_connection: MockConnection
    ) -> None:
        # The handle will wait 2 seconds, but timeout is 1 second,
        # so asyncio.wait_for() will raise TimeoutError
        custom_handle = MockTerminalHandle(
            terminal_id="timeout_cleanup_terminal",
            wait_delay=2.0,  # Longer than the 1 second timeout
        )
        mock_connection._terminal_handle = custom_handle

        release_called = False

        async def mock_release() -> None:
            nonlocal release_called
            release_called = True

        custom_handle.release = mock_release

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="slow", timeout=1)
        # Timeout raises an error, but terminal should still be released
        try:
            await tool.run(args)
        except ToolError:
            pass

        assert release_called

    @pytest.mark.asyncio
    async def test_run_handles_release_failure(
        self, mock_connection: MockConnection
    ) -> None:
        custom_handle = MockTerminalHandle(terminal_id="release_failure_terminal")

        async def failing_release() -> None:
            raise RuntimeError("Release failed")

        custom_handle.release = failing_release
        mock_connection._terminal_handle = custom_handle

        tool = Bash(
            config=BashToolConfig(),
            state=AcpBashState.model_construct(
                connection=mock_connection,  # type: ignore[arg-type]
                session_id="test_session",
                tool_call_id="test_call",
            ),
        )

        args = BashArgs(command="test")
        # Should not raise, release failure is silently ignored
        result = await tool.run(args)

        assert result is not None
        assert result.stdout == "test output"
