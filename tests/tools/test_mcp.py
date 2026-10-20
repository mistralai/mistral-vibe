from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe import __version__
from vibe.core.config import MCPHttp, MCPStdio, MCPStreamableHttp, VibeConfigSchema
from vibe.core.tools.mcp import (
    AuthStatus,
    MCPRegistry,
    MCPToolResult,
    RemoteTool,
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
    create_vibe_mcp_http_client,
    list_tools_http,
    list_tools_stdio,
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


class TestMCPHttpClient:
    def test_create_vibe_mcp_http_client_uses_vibe_ssl_context(self):
        headers = {
            "Authorization": "Bearer token",
            "User-Agent": "MistralAI-MCPClient/1.0",
        }
        ssl_context = object()
        fake_client = object()
        with (
            patch(
                "vibe.core.tools.mcp.tools.build_ssl_context", return_value=ssl_context
            ),
            patch(
                "vibe.core.tools.mcp.tools.VibeAsyncHTTPClient",
                return_value=fake_client,
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

    def test_create_vibe_mcp_http_client_sets_vibe_cli_user_agent_by_default(self):
        with patch(
            "vibe.core.tools.mcp.tools.VibeAsyncHTTPClient", return_value=object()
        ) as async_client:
            create_vibe_mcp_http_client({"Authorization": "Bearer token"})

        kwargs = async_client.call_args.kwargs
        assert kwargs["headers"]["User-Agent"] == f"MistralAI-VibeCLI/{__version__}"

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
            patch("mcp.client.streamable_http.streamable_http_client", fake_stream),
            patch("mcp.ClientSession", _FakeMCPClientSession),
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

    def test_mcp_stdio_argv_keeps_windows_path_backslashes(self, monkeypatch):
        monkeypatch.setattr("vibe.core.config.models.is_windows", lambda: True)
        config = MCPStdio(
            name="test",
            transport="stdio",
            command='"C:\\Program Files\\dotnet\\dotnet.exe" C:\\srv\\server.dll',
            args=["--port", "8080"],
        )

        assert config.argv() == [
            "C:\\Program Files\\dotnet\\dotnet.exe",
            "C:\\srv\\server.dll",
            "--port",
            "8080",
        ]

    def test_mcp_stdio_argv_keeps_posix_escapes_off_windows(self, monkeypatch):
        monkeypatch.setattr("vibe.core.config.models.is_windows", lambda: False)
        config = MCPStdio(
            name="test", transport="stdio", command="/opt/my\\ server/run --flag"
        )

        assert config.argv() == ["/opt/my server/run", "--flag"]

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
        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", return_value=[remote]
        ) as discover:
            first = registry.get_tools([srv])
            second = registry.get_tools([srv])

        assert "cached_tool_a" in first
        assert first["cached_tool_a"] is second["cached_tool_a"]
        discover.assert_called_once()

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

    def test_clear_drops_failed(self):
        registry = MCPRegistry()
        registry._failed["stale"] = "boom"

        registry.clear()

        assert registry.pop_failed() == {}

    def test_count_loaded_excludes_failed_servers(self):
        registry = MCPRegistry()
        ok_srv = self._make_http_server("ok", url="http://ok:1")
        fail_srv = self._make_http_server("fail", url="http://fail:2")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            return_value=[RemoteTool(name="t")],
        ):
            registry.get_tools([ok_srv])

        assert registry.count_loaded([ok_srv, fail_srv]) == 1
        assert registry.count_loaded([ok_srv]) == 1
        assert registry.count_loaded([fail_srv]) == 0
        assert registry.count_loaded([]) == 0

    def test_cache_survives_multiple_get_tools_calls(self):
        registry = MCPRegistry()
        srv = self._make_http_server("stable")
        remote = RemoteTool(name="t1")
        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", return_value=[remote]
        ):
            first = registry.get_tools([srv])
            second = registry.get_tools([srv])

        assert first == second
        assert first["stable_t1"] is second["stable_t1"]

    def test_failed_forced_refresh_cannot_fall_back_to_stale_cache(self):
        registry = MCPRegistry()
        server = self._make_http_server("stable")
        discovery = AsyncMock(
            side_effect=[
                [RemoteTool(name="old")],
                ConnectionError("temporarily unavailable"),
                [RemoteTool(name="new")],
            ]
        )

        with patch("vibe.core.tools.mcp.registry.list_tools_http", new=discovery):
            assert set(registry.get_tools([server])) == {"stable_old"}
            registry.invalidate(server.name)
            assert registry.get_tools([server]) == {}
            assert set(registry.get_tools([server])) == {"stable_new"}

        assert discovery.call_count == 3

    def test_disjoint_server_lists_across_agents(self):
        registry = MCPRegistry()

        srv_x = self._make_http_server("x", url="http://x:1")
        srv_y = self._make_http_server("y", url="http://y:2")

        async def discover(url: str, **_kwargs: object) -> list[RemoteTool]:
            return [RemoteTool(name="tx" if url == "http://x:1" else "ty")]

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", side_effect=discover
        ):
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
            tools = await registry.get_tools_async([srv])

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
            tools = await registry.get_tools_async([srv])

        assert tools == {}
        assert registry.pop_failed() == {"fail": "down"}

    @pytest.mark.asyncio
    async def test_discover_stdio_success(self):
        registry = MCPRegistry()
        srv = self._make_stdio_server("local", command="python -m local_srv")
        remote = RemoteTool(name="run", description="Run it")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_stdio", return_value=[remote]
        ):
            tools = await registry.get_tools_async([srv])

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
        assert registry.pop_failed() == {"broken": "no binary"}

    @pytest.mark.asyncio
    async def test_exception_group_single_error_is_unwrapped(self):
        registry = MCPRegistry()
        srv = self._make_http_server("grouped", url="http://grouped:1")

        # Simulate the MCP library wrapping errors in ExceptionGroup
        wrapped_error = ExceptionGroup(
            "unhandled errors in a TaskGroup", [ConnectionError("connection refused")]
        )

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", side_effect=wrapped_error
        ):
            tools = await registry.get_tools_async([srv])

        assert tools == {}
        # After our change, this should extract the inner error message
        assert registry.pop_failed() == {"grouped": "connection refused"}

    @pytest.mark.asyncio
    async def test_exception_group_multiple_errors_are_joined(self):
        registry = MCPRegistry()
        srv = self._make_http_server("multi", url="http://multi:1")

        wrapped_error = ExceptionGroup(
            "multiple errors",
            [ConnectionError("connection refused"), TimeoutError("timed out")],
        )

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", side_effect=wrapped_error
        ):
            tools = await registry.get_tools_async([srv])

        assert tools == {}
        # Should join multiple error messages with "; "
        assert registry.pop_failed() == {"multi": "connection refused; timed out"}

    @pytest.mark.asyncio
    async def test_nested_base_exception_group_is_unwrapped_recursively(self):
        registry = MCPRegistry()
        srv = self._make_http_server("nested", url="http://nested:1")

        inner = BaseExceptionGroup(
            "inner", [ConnectionError("connection refused"), TimeoutError("timed out")]
        )
        wrapped_error = BaseExceptionGroup(
            "unhandled errors in a TaskGroup", [inner, RuntimeError("boom")]
        )

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", side_effect=wrapped_error
        ):
            tools = await registry.get_tools_async([srv])

        assert tools == {}
        assert registry.pop_failed() == {
            "nested": "connection refused; timed out; boom"
        }

    @pytest.mark.asyncio
    async def test_exception_group_drops_empty_cancelled_error(self):
        registry = MCPRegistry()
        wrapped_error = BaseExceptionGroup(
            "unhandled errors in a TaskGroup",
            [ConnectionError("connection refused"), asyncio.CancelledError()],
        )

        formatted = registry._format_mcp_error(wrapped_error)

        assert formatted == "connection refused"

    def test_bare_cancelled_error_falls_back_to_type_name(self):
        registry = MCPRegistry()

        formatted = registry._format_failed(asyncio.CancelledError())

        assert formatted == "CancelledError"

    def test_get_tools_discovers_only_uncached(self):
        registry = MCPRegistry()

        cached_srv = self._make_http_server("cached", url="http://c:1")
        new_srv = self._make_http_server("new", url="http://n:2")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            return_value=[RemoteTool(name="ct")],
        ):
            registry.get_tools([cached_srv])

        new_remote = RemoteTool(name="nt")
        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", return_value=[new_remote]
        ) as discover:
            tools = registry.get_tools([cached_srv, new_srv])

        assert "cached_ct" in tools
        assert "new_nt" in tools
        assert len(registry._cache) == 2
        discover.assert_called_once()


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
            patch("mcp.client.stdio.stdio_client") as mock_client,
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.StdioServerParameters") as mock_params_cls,
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
            await registry.get_tools_async([srv])

        mock_list.assert_called_once_with(
            ["python", "-m", "srv"],
            env=None,
            cwd="/tmp/myproject",
            startup_timeout_sec=srv.startup_timeout_sec,
        )


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
    ) -> VibeConfigSchema:
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
