from __future__ import annotations

import logging
import os
import threading
import time
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
import pytest

from vibe.core.config import MCPHttp, MCPStdio, MCPStreamableHttp
from vibe.core.tools.mcp import (
    MCPRegistry,
    MCPToolResult,
    RemoteTool,
    _mcp_stderr_capture,
    _parse_call_result,
    _stderr_logger_thread,
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


class TestMCPStderrCapture:
    """Tests for _mcp_stderr_capture and _stderr_logger_thread."""

    @pytest.mark.asyncio
    async def test_mcp_stderr_capture_returns_writable_stream(self):
        async with _mcp_stderr_capture() as stream:
            assert stream is not None
            assert callable(getattr(stream, "write", None))
            stream.write("test\n")

    def test_stderr_logger_thread_logs_decoded_lines(self):
        r_fd, w_fd = os.pipe()
        try:
            vibe_logger = logging.getLogger("vibe")
            with patch.object(vibe_logger, "debug") as debug_mock:
                thread = threading.Thread(
                    target=_stderr_logger_thread, args=(r_fd,), daemon=True
                )
                thread.start()
                try:
                    w = os.fdopen(w_fd, "wb")
                    w_fd = -1
                    w.write(b"hello stderr\n")
                    w.write(b"second line\n")
                    w.close()
                    w = None
                finally:
                    time.sleep(0.05)
                debug_mock.assert_any_call("[MCP stderr] hello stderr")
                debug_mock.assert_any_call("[MCP stderr] second line")
        finally:
            if w_fd >= 0:
                try:
                    os.close(w_fd)
                except OSError:
                    pass
            try:
                os.close(r_fd)
            except OSError:
                pass

    @pytest.mark.asyncio
    async def test_mcp_stderr_capture_logs_written_data(self):
        vibe_logger = logging.getLogger("vibe")
        with patch.object(vibe_logger, "debug") as debug_mock:
            async with _mcp_stderr_capture() as stream:
                stream.write("captured line\n")
            time.sleep(0.05)
            debug_mock.assert_called_with("[MCP stderr] captured line")

    @pytest.mark.asyncio
    async def test_mcp_stderr_capture_ignores_empty_lines(self):
        vibe_logger = logging.getLogger("vibe")
        with patch.object(vibe_logger, "debug") as debug_mock:
            async with _mcp_stderr_capture() as stream:
                stream.write("\n\n")
            time.sleep(0.05)
            debug_mock.assert_not_called()


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


class TestMCPRegistry:
    def _make_http_server(
        self, name: str, url: str = "http://localhost:8080"
    ) -> MCPHttp:
        return MCPHttp(name=name, transport="http", url=url)

    def _make_stdio_server(self, name: str, command: str = "python -m srv") -> MCPStdio:
        return MCPStdio(name=name, transport="stdio", command=command)

    def test_server_key_is_stable(self):
        srv = self._make_http_server("s1")
        registry = MCPRegistry()

        assert registry._server_key(srv) == registry._server_key(srv)

    def test_different_configs_produce_different_keys(self):
        registry = MCPRegistry()
        s1 = self._make_http_server("s1", url="http://a:1")
        s2 = self._make_http_server("s2", url="http://b:2")

        assert registry._server_key(s1) != registry._server_key(s2)

    def test_get_tools_caches_discovery(self):
        registry = MCPRegistry()
        srv = self._make_http_server("cached")
        remote = RemoteTool(name="tool_a", description="A tool")
        proxy = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080", remote=remote, alias="cached"
        )

        key = registry._server_key(srv)
        registry._cache[key] = {proxy.get_name(): proxy}

        tools = registry.get_tools([srv])
        assert "cached_tool_a" in tools
        assert tools["cached_tool_a"] is proxy

    def test_get_tools_returns_empty_for_no_servers(self):
        registry = MCPRegistry()

        assert registry.get_tools([]) == {}

    def test_clear_drops_cache(self):
        registry = MCPRegistry()
        srv = self._make_http_server("s")
        proxy = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080", remote=RemoteTool(name="t"), alias="s"
        )
        key = registry._server_key(srv)
        registry._cache[key] = {proxy.get_name(): proxy}

        registry.clear()

        assert len(registry._cache) == 0

    def test_cache_survives_multiple_get_tools_calls(self):
        registry = MCPRegistry()
        srv = self._make_http_server("stable")
        remote = RemoteTool(name="t1")
        proxy = create_mcp_http_proxy_tool_class(
            url="http://localhost:8080", remote=remote, alias="stable"
        )

        key = registry._server_key(srv)
        registry._cache[key] = {proxy.get_name(): proxy}

        first = registry.get_tools([srv])
        second = registry.get_tools([srv])

        assert first == second
        assert first["stable_t1"] is second["stable_t1"]

    def test_disjoint_server_lists_across_agents(self):
        registry = MCPRegistry()

        srv_x = self._make_http_server("x", url="http://x:1")
        srv_y = self._make_http_server("y", url="http://y:2")

        proxy_x = create_mcp_http_proxy_tool_class(
            url="http://x:1", remote=RemoteTool(name="tx"), alias="x"
        )
        proxy_y = create_mcp_http_proxy_tool_class(
            url="http://y:2", remote=RemoteTool(name="ty"), alias="y"
        )

        registry._cache[registry._server_key(srv_x)] = {proxy_x.get_name(): proxy_x}
        registry._cache[registry._server_key(srv_y)] = {proxy_y.get_name(): proxy_y}

        agent_a_tools = registry.get_tools([srv_x])
        agent_b_tools = registry.get_tools([srv_y])

        assert "x_tx" in agent_a_tools
        assert "y_ty" not in agent_a_tools
        assert "y_ty" in agent_b_tools
        assert "x_tx" not in agent_b_tools

    @pytest.mark.asyncio
    async def test_discover_http_success(self):
        registry = MCPRegistry()
        srv = self._make_http_server("demo", url="http://demo:9090")
        remote = RemoteTool(name="hello", description="Hi")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", return_value=[remote]
        ):
            tools = await registry._discover_http(srv)

        assert tools is not None
        assert len(tools) == 1
        name = next(iter(tools))
        assert name == "demo_hello"

    @pytest.mark.asyncio
    async def test_discover_http_failure_returns_none(self):
        registry = MCPRegistry()
        srv = self._make_http_server("fail", url="http://fail:1")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            side_effect=ConnectionError("down"),
        ):
            tools = await registry._discover_http(srv)

        assert tools is None

    @pytest.mark.asyncio
    async def test_discover_stdio_success(self):
        registry = MCPRegistry()
        srv = self._make_stdio_server("local", command="python -m local_srv")
        remote = RemoteTool(name="run", description="Run it")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_stdio", return_value=[remote]
        ):
            tools = await registry._discover_stdio(srv)

        assert tools is not None
        assert len(tools) == 1
        name = next(iter(tools))
        assert name == "local_run"

    @pytest.mark.asyncio
    async def test_discover_stdio_failure_returns_none(self):
        registry = MCPRegistry()
        srv = self._make_stdio_server("broken")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_stdio",
            side_effect=OSError("no binary"),
        ):
            tools = await registry._discover_stdio(srv)

        assert tools is None

    def test_get_tools_discovers_only_uncached(self):
        registry = MCPRegistry()

        cached_srv = self._make_http_server("cached", url="http://c:1")
        new_srv = self._make_http_server("new", url="http://n:2")

        cached_proxy = create_mcp_http_proxy_tool_class(
            url="http://c:1", remote=RemoteTool(name="ct"), alias="cached"
        )
        registry._cache[registry._server_key(cached_srv)] = {
            cached_proxy.get_name(): cached_proxy
        }

        new_remote = RemoteTool(name="nt")
        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", return_value=[new_remote]
        ):
            tools = registry.get_tools([cached_srv, new_srv])

        assert "cached_ct" in tools
        assert "new_nt" in tools
        assert len(registry._cache) == 2
