from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
from pydantic import ValidationError
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.config import MCPHttp, MCPStdio, MCPStreamableHttp, VibeConfig
from vibe.core.tools.base import BaseToolConfig, BaseToolState, InvokeContext
from vibe.core.tools.mcp import (
    AuthStatus,
    MCPConnectionPool,
    MCPRegistry,
    MCPToolResult,
    RemoteTool,
    _mcp_stderr_capture,
    _parse_call_result,
    _stderr_logger_thread,
    call_tool_http,
    call_tool_stdio,
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
    create_vibe_mcp_http_client,
    list_tools_http,
    list_tools_stdio,
)
from vibe.core.tools.mcp.pool import _StdioConnection, stdio_key
from vibe.core.tools.mcp.tools import _OpenArgs, build_stdio_params


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


class TestMCPHttpClient:
    def test_create_vibe_mcp_http_client_uses_vibe_ssl_context(self):
        headers = {"Authorization": "Bearer token"}
        ssl_context = object()
        fake_client = object()
        with (
            patch(
                "vibe.core.tools.mcp.tools.build_ssl_context", return_value=ssl_context
            ),
            patch(
                "vibe.core.tools.mcp.tools.httpx.AsyncClient", return_value=fake_client
            ) as async_client,
        ):
            client = create_vibe_mcp_http_client(headers)

        assert client is fake_client
        kwargs = async_client.call_args.kwargs
        assert kwargs["follow_redirects"] is True
        assert kwargs["headers"] == headers
        assert kwargs["verify"] is ssl_context
        assert kwargs["timeout"].connect == 30.0
        assert kwargs["timeout"].read == 300.0

    @pytest.mark.asyncio
    async def test_list_tools_http_uses_vibe_mcp_http_client(self):
        fake_client = _FakeHttpClient()
        captured: dict[str, Any] = {}

        @contextlib.asynccontextmanager
        async def fake_stream(url: str, *, http_client: Any):
            captured["url"] = url
            captured["http_client"] = http_client
            yield object(), object(), lambda: None

        with (
            patch(
                "vibe.core.tools.mcp.tools.create_vibe_mcp_http_client",
                return_value=fake_client,
            ) as create_client,
            patch("vibe.core.tools.mcp.tools.streamable_http_client", fake_stream),
            patch("vibe.core.tools.mcp.tools.ClientSession", _FakeMCPClientSession),
        ):
            tools = await list_tools_http(
                "https://mcp.example.com",
                headers={"Authorization": "Bearer token"},
                startup_timeout_sec=42.0,
            )

        create_client.assert_called_once_with(
            {"Authorization": "Bearer token"}, auth=None
        )
        assert fake_client.entered is True
        assert fake_client.closed is True
        assert captured["url"] == "https://mcp.example.com"
        assert captured["http_client"] is fake_client
        assert [tool.name for tool in tools] == ["remote_tool"]

    @pytest.mark.asyncio
    async def test_call_tool_http_uses_vibe_mcp_http_client(self):
        fake_client = _FakeHttpClient()
        captured: dict[str, Any] = {}

        @contextlib.asynccontextmanager
        async def fake_stream(url: str, *, http_client: Any):
            captured["url"] = url
            captured["http_client"] = http_client
            yield object(), object(), lambda: None

        with (
            patch(
                "vibe.core.tools.mcp.tools.create_vibe_mcp_http_client",
                return_value=fake_client,
            ) as create_client,
            patch("vibe.core.tools.mcp.tools.streamable_http_client", fake_stream),
            patch("vibe.core.tools.mcp.tools.ClientSession", _FakeMCPClientSession),
        ):
            result = await call_tool_http(
                "https://mcp.example.com",
                "remote_tool",
                {"query": "hello"},
                headers={"Authorization": "Bearer token"},
                startup_timeout_sec=42.0,
                tool_timeout_sec=12.0,
            )

        create_client.assert_called_once_with(
            {"Authorization": "Bearer token"}, auth=None
        )
        assert fake_client.entered is True
        assert fake_client.closed is True
        assert captured["url"] == "https://mcp.example.com"
        assert captured["http_client"] is fake_client
        assert result.structured == {"ok": True}


class _FakeHttpClient:
    def __init__(self) -> None:
        self.entered = False
        self.closed = False

    async def __aenter__(self) -> _FakeHttpClient:
        self.entered = True
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.closed = True


class _FakeMCPClientSession:
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeMCPClientSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def initialize(self) -> None:
        pass

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[{"name": "remote_tool"}])

    async def call_tool(self, *_: Any, **__: Any) -> SimpleNamespace:
        return SimpleNamespace(structuredContent={"ok": True}, content=None)


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

    def test_get_tools_reconciles_status_with_active_servers(self):
        registry = MCPRegistry()
        kept = self._make_http_server("kept", url="http://kept:1")
        removed = self._make_http_server("removed", url="http://removed:1")
        kept_proxy = create_mcp_http_proxy_tool_class(
            url="http://kept:1", remote=RemoteTool(name="search"), alias="kept"
        )
        removed_proxy = create_mcp_http_proxy_tool_class(
            url="http://removed:1", remote=RemoteTool(name="search"), alias="removed"
        )
        registry._cache[registry._server_key(kept)] = {
            kept_proxy.get_name(): kept_proxy
        }
        registry._cache[registry._server_key(removed)] = {
            removed_proxy.get_name(): removed_proxy
        }

        registry.get_tools([kept, removed])
        registry.get_tools([kept])

        assert registry.status() == {"kept": AuthStatus.STATIC}

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

    def test_count_loaded_excludes_failed_servers(self):
        registry = MCPRegistry()
        ok_srv = self._make_http_server("ok", url="http://ok:1")
        fail_srv = self._make_http_server("fail", url="http://fail:2")

        proxy = create_mcp_http_proxy_tool_class(
            url="http://ok:1", remote=RemoteTool(name="t"), alias="ok"
        )
        registry._cache[registry._server_key(ok_srv)] = {proxy.get_name(): proxy}

        assert registry.count_loaded([ok_srv, fail_srv]) == 1
        assert registry.count_loaded([ok_srv]) == 1
        assert registry.count_loaded([fail_srv]) == 0
        assert registry.count_loaded([]) == 0

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


class TestMCPStdioCwd:
    def test_mcp_stdio_cwd_defaults_to_none(self):
        config = MCPStdio(name="test", transport="stdio", command="python -m srv")

        assert config.cwd is None

    def test_mcp_stdio_cwd_accepts_string(self):
        config = MCPStdio(
            name="test",
            transport="stdio",
            command="python -m srv",
            cwd="/tmp/myproject",
        )

        assert config.cwd == "/tmp/myproject"

    @pytest.mark.asyncio
    async def test_list_tools_stdio_passes_cwd_to_params(self):
        with (
            patch("vibe.core.tools.mcp.tools.stdio_client") as mock_client,
            patch("vibe.core.tools.mcp.tools.ClientSession") as mock_session_cls,
            patch("vibe.core.tools.mcp.tools.StdioServerParameters") as mock_params_cls,
        ):
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=(MagicMock(), MagicMock())
            )
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session = MagicMock()
            mock_session.initialize = AsyncMock()
            mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[]))
            mock_session_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await list_tools_stdio(["python", "-m", "srv"], cwd="/tmp/myproject")

            mock_params_cls.assert_called_once_with(
                command="python", args=["-m", "srv"], env=None, cwd="/tmp/myproject"
            )

    @pytest.mark.asyncio
    async def test_call_tool_stdio_passes_cwd_to_params(self):
        with (
            patch("vibe.core.tools.mcp.tools.stdio_client") as mock_client,
            patch("vibe.core.tools.mcp.tools.ClientSession") as mock_session_cls,
            patch("vibe.core.tools.mcp.tools.StdioServerParameters") as mock_params_cls,
            patch("vibe.core.tools.mcp.tools._parse_call_result") as mock_parse,
        ):
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=(MagicMock(), MagicMock())
            )
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_session = MagicMock()
            mock_session.initialize = AsyncMock()
            mock_session.call_tool = AsyncMock(return_value=MagicMock())
            mock_session_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_parse.return_value = MagicMock(spec=MCPToolResult)

            await call_tool_stdio(
                ["python", "-m", "srv"], "my_tool", {}, cwd="/tmp/myproject"
            )

            mock_params_cls.assert_called_once_with(
                command="python", args=["-m", "srv"], env=None, cwd="/tmp/myproject"
            )

    @pytest.mark.asyncio
    async def test_discover_stdio_passes_cwd_to_list_tools(self):
        registry = MCPRegistry()
        srv = MCPStdio(
            name="local",
            transport="stdio",
            command="python -m srv",
            cwd="/tmp/myproject",
        )
        remote = RemoteTool(name="run", description="Run it")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_stdio", return_value=[remote]
        ) as mock_list:
            await registry._discover_stdio(srv)

        mock_list.assert_called_once_with(
            ["python", "-m", "srv"],
            env=None,
            cwd="/tmp/myproject",
            startup_timeout_sec=srv.startup_timeout_sec,
        )

    @pytest.mark.asyncio
    async def test_discover_stdio_passes_cwd_to_proxy_class(self):
        registry = MCPRegistry()
        srv = MCPStdio(
            name="local",
            transport="stdio",
            command="python -m srv",
            cwd="/tmp/myproject",
        )
        remote = RemoteTool(name="run", description="Run it")

        with (
            patch(
                "vibe.core.tools.mcp.registry.list_tools_stdio", return_value=[remote]
            ),
            patch(
                "vibe.core.tools.mcp.registry.create_mcp_stdio_proxy_tool_class",
                wraps=create_mcp_stdio_proxy_tool_class,
            ) as mock_create,
        ):
            await registry._discover_stdio(srv)

        _, kwargs = mock_create.call_args
        assert kwargs["cwd"] == "/tmp/myproject"

    def test_proxy_tool_stores_cwd(self):
        remote = RemoteTool(name="run")
        proxy_cls = cast(
            Any,
            create_mcp_stdio_proxy_tool_class(
                command=["python", "-m", "srv"], remote=remote, cwd="/tmp/myproject"
            ),
        )

        assert proxy_cls._cwd == "/tmp/myproject"

    def test_proxy_tool_cwd_defaults_to_none(self):
        remote = RemoteTool(name="run")
        proxy_cls = cast(
            Any,
            create_mcp_stdio_proxy_tool_class(
                command=["python", "-m", "srv"], remote=remote
            ),
        )

        assert proxy_cls._cwd is None


# ---------------------------------------------------------------------------
# _MCPBase disabled / disabled_tools field tests
# ---------------------------------------------------------------------------


class TestMCPBaseDisableFields:
    def test_disabled_defaults_to_false(self):
        config = MCPStdio(name="test", transport="stdio", command="python")
        assert config.disabled is False
        assert config.disabled_tools == []

    def test_disabled_true(self):
        config = MCPStdio(
            name="test", transport="stdio", command="python", disabled=True
        )
        assert config.disabled is True

    def test_disabled_tools_list(self):
        config = MCPHttp(
            name="test",
            transport="http",
            url="http://localhost:8080",
            disabled_tools=["search", "read"],
        )
        assert config.disabled_tools == ["search", "read"]

    def test_disabled_fields_on_streamable_http(self):
        config = MCPStreamableHttp(
            name="test",
            transport="streamable-http",
            url="http://localhost:8080",
            disabled=True,
            disabled_tools=["write"],
        )
        assert config.disabled is True
        assert config.disabled_tools == ["write"]


# ---------------------------------------------------------------------------
# ToolManager: per-MCP-server disabled / disabled_tools filtering
# ---------------------------------------------------------------------------

from vibe.core.tools.manager import ToolManager


class TestMCPDisableFiltering:
    @staticmethod
    def _make_config(
        mcp_servers: list[MCPHttp | MCPStdio | MCPStreamableHttp] | None = None,
    ) -> VibeConfig:
        return build_test_vibe_config(mcp_servers=mcp_servers or [])

    def test_disabled_server_excludes_all_tools(self):
        srv = MCPHttp(
            name="demo", transport="http", url="http://demo:9090", disabled=True
        )
        registry = FakeMCPRegistry()
        remote_a = RemoteTool(name="tool_a", description="A")
        remote_b = RemoteTool(name="tool_b", description="B")
        proxy_a = create_mcp_http_proxy_tool_class(
            url="http://demo:9090", remote=remote_a, alias="demo"
        )
        proxy_b = create_mcp_http_proxy_tool_class(
            url="http://demo:9090", remote=remote_b, alias="demo"
        )
        registry.set_tools(
            [srv], {proxy_a.get_name(): proxy_a, proxy_b.get_name(): proxy_b}
        )

        config = self._make_config(mcp_servers=[srv])
        tm = ToolManager(
            config_getter=lambda: config, mcp_registry=registry, connector_registry=None
        )
        assert "demo_tool_a" not in tm.available_tools
        assert "demo_tool_b" not in tm.available_tools
        # Still registered (discoverable for UI)
        assert "demo_tool_a" in tm.registered_tools

    def test_disabled_tools_filters_specific_tools(self):
        srv = MCPHttp(
            name="demo",
            transport="http",
            url="http://demo:9090",
            disabled_tools=["tool_a"],
        )
        registry = FakeMCPRegistry()
        remote_a = RemoteTool(name="tool_a", description="A")
        remote_b = RemoteTool(name="tool_b", description="B")
        proxy_a = create_mcp_http_proxy_tool_class(
            url="http://demo:9090", remote=remote_a, alias="demo"
        )
        proxy_b = create_mcp_http_proxy_tool_class(
            url="http://demo:9090", remote=remote_b, alias="demo"
        )
        registry.set_tools(
            [srv], {proxy_a.get_name(): proxy_a, proxy_b.get_name(): proxy_b}
        )

        config = self._make_config(mcp_servers=[srv])
        tm = ToolManager(
            config_getter=lambda: config, mcp_registry=registry, connector_registry=None
        )
        assert "demo_tool_a" not in tm.available_tools
        assert "demo_tool_b" in tm.available_tools

    def test_disabled_false_is_noop(self):
        srv = MCPHttp(
            name="demo", transport="http", url="http://demo:9090", disabled=False
        )
        registry = FakeMCPRegistry()
        remote = RemoteTool(name="tool_a", description="A")
        proxy = create_mcp_http_proxy_tool_class(
            url="http://demo:9090", remote=remote, alias="demo"
        )
        registry.set_tools([srv], {proxy.get_name(): proxy})

        config = self._make_config(mcp_servers=[srv])
        tm = ToolManager(
            config_getter=lambda: config, mcp_registry=registry, connector_registry=None
        )
        assert "demo_tool_a" in tm.available_tools


def _ok_result(value: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(structuredContent=value or {"ok": 1}, content=None)


class _FakeSession:
    def __init__(self, call_tool: AsyncMock) -> None:
        self.call_tool = call_tool


def _patch_enter(sessions: list[_FakeSession], closed: list[_FakeSession]) -> AsyncMock:
    it = iter(sessions)

    async def _record_close(session: _FakeSession) -> None:
        closed.append(session)

    async def _enter(stack, params, *, init_timeout, sampling_callback=None):
        session = next(it)
        stack.push_async_callback(_record_close, session)
        return session

    return AsyncMock(side_effect=_enter)


class TestMCPConnectionPool:
    @pytest.mark.asyncio
    async def test_reuses_connection_across_calls(self):
        call_tool = AsyncMock(return_value=_ok_result())
        session = _FakeSession(call_tool)
        enter = _patch_enter([session], [])
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            r1 = await pool.call_tool(
                command=["srv"], tool_name="t", arguments={"a": 1}
            )
            r2 = await pool.call_tool(
                command=["srv"], tool_name="t", arguments={"a": 2}
            )

        assert enter.call_count == 1
        assert call_tool.await_count == 2
        assert isinstance(r1, MCPToolResult) and r1.structured == {"ok": 1}
        assert r2.structured == {"ok": 1}

    @pytest.mark.asyncio
    async def test_serializes_concurrent_calls_to_same_connection(self):
        active = 0
        max_active = 0
        order: list[int] = []

        async def _call(tool_name, arguments, read_timeout_seconds=None):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            order.append(arguments["i"])
            active -= 1
            return _ok_result()

        session = _FakeSession(AsyncMock(side_effect=_call))
        enter = _patch_enter([session], [])
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            await asyncio.gather(
                *(
                    pool.call_tool(command=["srv"], tool_name="t", arguments={"i": i})
                    for i in range(5)
                )
            )

        assert enter.call_count == 1
        assert max_active == 1
        assert order == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_reconnects_once_on_transport_death(self):
        dead = AsyncMock(side_effect=anyio.ClosedResourceError())
        alive = AsyncMock(return_value=_ok_result({"recovered": 1}))
        sessions = [_FakeSession(dead), _FakeSession(alive)]
        enter = _patch_enter(sessions, [])
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            result = await pool.call_tool(command=["srv"], tool_name="t", arguments={})

        assert enter.call_count == 2
        assert result.structured == {"recovered": 1}

    @pytest.mark.asyncio
    async def test_second_transport_failure_propagates(self):
        dead1 = AsyncMock(side_effect=anyio.ClosedResourceError())
        dead2 = AsyncMock(side_effect=anyio.BrokenResourceError())
        sessions = [_FakeSession(dead1), _FakeSession(dead2)]
        enter = _patch_enter(sessions, [])
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            with pytest.raises(anyio.BrokenResourceError):
                await pool.call_tool(command=["srv"], tool_name="t", arguments={})

        assert enter.call_count == 2

    @pytest.mark.asyncio
    async def test_distinct_keys_get_distinct_connections(self):
        sessions = [
            _FakeSession(AsyncMock(return_value=_ok_result())),
            _FakeSession(AsyncMock(return_value=_ok_result())),
        ]
        enter = _patch_enter(sessions, [])
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            await pool.call_tool(command=["srv-a"], tool_name="t", arguments={})
            await pool.call_tool(command=["srv-b"], tool_name="t", arguments={})

        assert enter.call_count == 2

    @pytest.mark.asyncio
    async def test_same_key_different_env_get_distinct_connections(self):
        sessions = [
            _FakeSession(AsyncMock(return_value=_ok_result())),
            _FakeSession(AsyncMock(return_value=_ok_result())),
        ]
        enter = _patch_enter(sessions, [])
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            await pool.call_tool(
                command=["srv"], tool_name="t", arguments={}, env={"K": "1"}
            )
            await pool.call_tool(
                command=["srv"], tool_name="t", arguments={}, env={"K": "2"}
            )

        assert enter.call_count == 2

    @pytest.mark.asyncio
    async def test_aclose_closes_all_connections(self):
        closed: list[_FakeSession] = []
        sessions = [
            _FakeSession(AsyncMock(return_value=_ok_result())),
            _FakeSession(AsyncMock(return_value=_ok_result())),
        ]
        enter = _patch_enter(sessions, closed)
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            await pool.call_tool(command=["srv-a"], tool_name="t", arguments={})
            await pool.call_tool(command=["srv-b"], tool_name="t", arguments={})
            await pool.aclose()

        assert closed == sessions
        assert pool._conns == {}

    @pytest.mark.asyncio
    async def test_tool_error_does_not_reconnect(self):
        err = RuntimeError("tool blew up")
        session = _FakeSession(AsyncMock(side_effect=err))
        enter = _patch_enter([session], [])
        pool = MCPConnectionPool()

        with patch("vibe.core.tools.mcp.pool.enter_stdio_session", enter):
            with pytest.raises(RuntimeError, match="tool blew up"):
                await pool.call_tool(command=["srv"], tool_name="t", arguments={})

        assert enter.call_count == 1

    @pytest.mark.asyncio
    async def test_bind_loop_drops_connections_on_loop_change(self):
        pool = MCPConnectionPool()
        other_loop = asyncio.new_event_loop()
        try:
            pool._loop = other_loop
            pool._conns["k"] = _StdioConnection(build_stdio_params(["srv"]), None, None)
            pool._bind_loop()
            assert pool._conns == {}
            assert pool._loop is asyncio.get_running_loop()
        finally:
            other_loop.close()

    def test_stdio_key_stable_and_distinct(self):
        base = stdio_key(["srv", "--x"], {"A": "1"}, "/tmp")
        assert base == stdio_key(["srv", "--x"], {"A": "1"}, "/tmp")
        assert base != stdio_key(["srv", "--y"], {"A": "1"}, "/tmp")
        assert base != stdio_key(["srv", "--x"], {"A": "2"}, "/tmp")
        assert base != stdio_key(["srv", "--x"], {"A": "1"}, "/other")


class TestMCPStdioProxyToolPooling:
    @staticmethod
    def _make_tool():
        cls = create_mcp_stdio_proxy_tool_class(
            command=["srv"], remote=RemoteTool(name="t"), alias="local"
        )
        return cls(lambda: BaseToolConfig(), BaseToolState())

    @pytest.mark.asyncio
    async def test_run_routes_through_pool_when_present(self):
        tool = self._make_tool()
        pool = MagicMock()
        pool.call_tool = AsyncMock(
            return_value=MCPToolResult(server="s", tool="t", text="ok")
        )
        ctx = InvokeContext(tool_call_id="1", mcp_pool=pool)

        with patch("vibe.core.tools.mcp.tools.call_tool_stdio") as one_shot:
            results = [ev async for ev in tool.run(_OpenArgs(), ctx)]

        one_shot.assert_not_called()
        pool.call_tool.assert_awaited_once()
        assert isinstance(results[0], MCPToolResult)
        assert results[0].text == "ok"

    @pytest.mark.asyncio
    async def test_run_falls_back_without_pool(self):
        tool = self._make_tool()
        ctx = InvokeContext(tool_call_id="1", mcp_pool=None)

        with patch(
            "vibe.core.tools.mcp.tools.call_tool_stdio",
            AsyncMock(return_value=MCPToolResult(server="s", tool="t", text="fb")),
        ) as one_shot:
            results = [ev async for ev in tool.run(_OpenArgs(), ctx)]

        one_shot.assert_awaited_once()
        assert isinstance(results[0], MCPToolResult)
        assert results[0].text == "fb"


_COUNTER_SERVER = """
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("counter")
_state = {"n": 0}


@mcp.tool()
def increment() -> str:
    _state["n"] += 1
    return f"count={_state['n']} pid={os.getpid()}"


if __name__ == "__main__":
    mcp.run()
"""


def _result_text(result: MCPToolResult) -> str:
    if result.text:
        return result.text
    return str(result.structured)


class TestMCPConnectionPoolIntegration:
    @pytest.mark.asyncio
    async def test_persists_real_subprocess_state_across_calls(self, tmp_path):
        script = tmp_path / "counter_server.py"
        script.write_text(_COUNTER_SERVER)
        command = [sys.executable, str(script)]
        pool = MCPConnectionPool()

        try:
            r1 = await pool.call_tool(
                command=command,
                tool_name="increment",
                arguments={},
                startup_timeout_sec=30,
                tool_timeout_sec=30,
            )
            r2 = await pool.call_tool(
                command=command,
                tool_name="increment",
                arguments={},
                startup_timeout_sec=30,
                tool_timeout_sec=30,
            )
        finally:
            await pool.aclose()

        t1, t2 = _result_text(r1), _result_text(r2)
        # State survives across calls only if the same subprocess handled both.
        assert "count=1" in t1
        assert "count=2" in t2
        pid1 = re.search(r"pid=(\d+)", t1)
        pid2 = re.search(r"pid=(\d+)", t2)
        assert pid1 is not None and pid2 is not None
        assert pid1.group(1) == pid2.group(1)

    @pytest.mark.asyncio
    async def test_aclose_terminates_real_subprocess(self, tmp_path):
        script = tmp_path / "counter_server.py"
        script.write_text(_COUNTER_SERVER)
        command = [sys.executable, str(script)]
        pool = MCPConnectionPool()

        result = await pool.call_tool(
            command=command,
            tool_name="increment",
            arguments={},
            startup_timeout_sec=30,
            tool_timeout_sec=30,
        )
        pid_match = re.search(r"pid=(\d+)", _result_text(result))
        assert pid_match is not None
        pid = int(pid_match.group(1))

        await pool.aclose()

        # The subprocess should be gone after aclose; poll briefly to allow the
        # OS to reap it.
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail(f"MCP subprocess pid={pid} still alive after aclose")
