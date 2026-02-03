from __future__ import annotations

from unittest.mock import MagicMock

from pydantic import ValidationError
import pytest

from vibe.core.config import MCPHttp, MCPStdio, MCPStreamableHttp
from vibe.core.tools.mcp import (
    MCPToolResult,
    RemoteTool,
    _parse_call_result,
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
)


class TestRemoteTool:
    def test_creates_remote_tool_with_valid_data(self):
        tool = RemoteTool.model_validate({
            "name": "test_tool",
            "description": "A test tool",
            "inputSchema": {
                "type": "object",
                "properties": {"arg": {"type": "string"}},
            },
        })

        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.input_schema == {
            "type": "object",
            "properties": {"arg": {"type": "string"}},
        }

    def test_uses_default_schema_when_none_provided(self):
        tool = RemoteTool(name="test_tool")

        assert tool.input_schema == {"type": "object", "properties": {}}

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="MCP tool missing valid 'name'"):
            RemoteTool(name="")

    def test_rejects_whitespace_only_name(self):
        with pytest.raises(ValueError, match="MCP tool missing valid 'name'"):
            RemoteTool(name="   ")

    def test_normalizes_schema_from_object_with_model_dump(self):
        mock_schema = MagicMock()
        mock_schema.model_dump.return_value = {"type": "string"}

        tool = RemoteTool.model_validate({"name": "test", "inputSchema": mock_schema})

        assert tool.input_schema == {"type": "string"}

    def test_rejects_invalid_input_schema(self):
        with pytest.raises(ValueError, match="inputSchema must be a dict"):
            RemoteTool.model_validate({"name": "test", "inputSchema": 12345})


class TestMCPToolResult:
    def test_creates_result_with_text(self):
        result = MCPToolResult(server="test_server", tool="test_tool", text="output")

        assert result.ok is True
        assert result.server == "test_server"
        assert result.tool == "test_tool"
        assert result.text == "output"
        assert result.structured is None

    def test_creates_result_with_structured_content(self):
        result = MCPToolResult(
            server="test_server", tool="test_tool", structured={"key": "value"}
        )

        assert result.structured == {"key": "value"}
        assert result.text is None


class TestParseCallResult:
    def test_parses_text_content(self):
        mock_result = MagicMock()
        mock_result.structuredContent = None
        mock_result.content = [MagicMock(text="Hello world")]

        result = _parse_call_result("server", "tool", mock_result)

        assert result.server == "server"
        assert result.tool == "tool"
        assert result.text == "Hello world"
        assert result.structured is None

    def test_parses_structured_content(self):
        mock_result = MagicMock()
        mock_result.structuredContent = {"data": "value"}
        mock_result.content = None

        result = _parse_call_result("server", "tool", mock_result)

        assert result.structured == {"data": "value"}
        assert result.text is None

    def test_prefers_structured_over_text(self):
        mock_result = MagicMock()
        mock_result.structuredContent = {"data": "value"}
        mock_result.content = [MagicMock(text="text content")]

        result = _parse_call_result("server", "tool", mock_result)

        assert result.structured == {"data": "value"}
        assert result.text is None

    def test_joins_multiple_text_blocks(self):
        mock_result = MagicMock()
        mock_result.structuredContent = None
        mock_result.content = [MagicMock(text="line1"), MagicMock(text="line2")]

        result = _parse_call_result("server", "tool", mock_result)

        assert result.text == "line1\nline2"


class TestCreateMCPHttpProxyToolClass:
    def test_creates_tool_class_with_correct_name(self):
        remote = RemoteTool(name="my_tool", description="Test tool")
        tool_cls = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080", remote=remote, alias="test_server"
        )

        assert tool_cls.get_name() == "test_server_my_tool"

    def test_creates_tool_class_with_url_based_alias(self):
        remote = RemoteTool(name="my_tool")
        tool_cls = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080", remote=remote
        )

        assert tool_cls.get_name() == "localhost_8080_my_tool"

    def test_includes_description_with_hint(self):
        remote = RemoteTool(name="my_tool", description="Base description")
        tool_cls = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080",
            remote=remote,
            alias="test",
            server_hint="Use this for testing",
        )

        assert "[test]" in tool_cls.description
        assert "Base description" in tool_cls.description
        assert "Hint: Use this for testing" in tool_cls.description

    def test_stores_timeout_settings(self):
        remote = RemoteTool(name="my_tool")
        tool_cls = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080",
            remote=remote,
            startup_timeout_sec=30.0,
            tool_timeout_sec=120.0,
        )

        assert tool_cls._startup_timeout_sec == 30.0  # type: ignore[attr-defined]
        assert tool_cls._tool_timeout_sec == 120.0  # type: ignore[attr-defined]

    def test_returns_correct_parameters(self):
        remote = RemoteTool.model_validate({
            "name": "my_tool",
            "inputSchema": {
                "type": "object",
                "properties": {"arg": {"type": "string"}},
            },
        })
        tool_cls = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080", remote=remote
        )

        params = tool_cls.get_parameters()

        assert params == {"type": "object", "properties": {"arg": {"type": "string"}}}


class TestCreateMCPStdioProxyToolClass:
    def test_creates_tool_class_with_alias(self):
        remote = RemoteTool(name="my_tool")
        tool_cls = create_mcp_stdio_proxy_tool_class(
            command=["python", "-m", "mcp_server"], remote=remote, alias="my_server"
        )

        assert tool_cls.get_name() == "my_server_my_tool"

    def test_creates_tool_class_with_command_based_alias(self):
        remote = RemoteTool(name="my_tool")
        tool_cls = create_mcp_stdio_proxy_tool_class(
            command=["python", "-m", "mcp_server"], remote=remote
        )

        name = tool_cls.get_name()
        assert name.startswith("python_")
        assert name.endswith("_my_tool")

    def test_stores_env_settings(self):
        remote = RemoteTool(name="my_tool")
        tool_cls = create_mcp_stdio_proxy_tool_class(
            command=["python", "-m", "mcp_server"],
            remote=remote,
            env={"API_KEY": "secret"},
        )

        assert tool_cls._env == {"API_KEY": "secret"}  # type: ignore[attr-defined]

    def test_stores_timeout_settings(self):
        remote = RemoteTool(name="my_tool")
        tool_cls = create_mcp_stdio_proxy_tool_class(
            command=["python", "-m", "mcp_server"],
            remote=remote,
            startup_timeout_sec=15.0,
            tool_timeout_sec=90.0,
        )

        assert tool_cls._startup_timeout_sec == 15.0  # type: ignore[attr-defined]
        assert tool_cls._tool_timeout_sec == 90.0  # type: ignore[attr-defined]

    def test_includes_hint_in_description(self):
        remote = RemoteTool(name="my_tool", description="Base description")
        tool_cls = create_mcp_stdio_proxy_tool_class(
            command=["python"],
            remote=remote,
            alias="test",
            server_hint="For testing only",
        )

        assert "Hint: For testing only" in tool_cls.description


class TestMCPConfigModels:
    def test_mcp_base_default_timeouts(self):
        config = MCPStdio(
            name="test", transport="stdio", command="python -m test_server"
        )

        assert config.startup_timeout_sec == 10.0
        assert config.tool_timeout_sec == 60.0

    def test_mcp_base_custom_timeouts(self):
        config = MCPStdio(
            name="test",
            transport="stdio",
            command="python -m test_server",
            startup_timeout_sec=30.0,
            tool_timeout_sec=120.0,
        )

        assert config.startup_timeout_sec == 30.0
        assert config.tool_timeout_sec == 120.0

    def test_mcp_base_rejects_non_positive_timeout(self):
        with pytest.raises(ValidationError):
            MCPStdio(
                name="test", transport="stdio", command="python", startup_timeout_sec=0
            )

    def test_mcp_stdio_with_env(self):
        config = MCPStdio(
            name="test",
            transport="stdio",
            command="python -m server",
            env={"API_KEY": "secret", "DEBUG": "1"},
        )

        assert config.env == {"API_KEY": "secret", "DEBUG": "1"}

    def test_mcp_stdio_argv_with_string_command(self):
        config = MCPStdio(
            name="test", transport="stdio", command="python -m server --port 8080"
        )

        assert config.argv() == ["python", "-m", "server", "--port", "8080"]

    def test_mcp_stdio_argv_with_list_command(self):
        config = MCPStdio(
            name="test",
            transport="stdio",
            command=["python", "-m", "server"],
            args=["--port", "8080"],
        )

        assert config.argv() == ["python", "-m", "server", "--port", "8080"]

    def test_mcp_http_default_timeouts(self):
        config = MCPHttp(name="test", transport="http", url="http://localhost:8080")

        assert config.startup_timeout_sec == 10.0
        assert config.tool_timeout_sec == 60.0

    def test_mcp_streamable_http_default_timeouts(self):
        config = MCPStreamableHttp(
            name="test", transport="streamable-http", url="http://localhost:8080"
        )

        assert config.startup_timeout_sec == 10.0
        assert config.tool_timeout_sec == 60.0

    def test_mcp_name_normalization(self):
        config = MCPStdio(name="my server!@#$%", transport="stdio", command="python")

        # Trailing special chars become underscores which are then stripped
        assert config.name == "my_server"
