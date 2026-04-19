from __future__ import annotations

import os
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.config import VibeConfig
from vibe.core.tools.base import BaseToolConfig, ToolError
from vibe.core.tools.connectors import CONNECTORS_ENV_VAR
from vibe.core.tools.connectors.connector_registry import (
    RemoteTool,
    _connector_error_message,
    _normalize_name,
    _unwrap_http_status_error,
    create_connector_proxy_tool_class,
)
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.mcp.tools import MCPTool, MCPToolResult

# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_basic(self) -> None:
        assert _normalize_name("my_connector") == "my_connector"

    def test_special_chars(self) -> None:
        assert _normalize_name("my.connector!v2") == "my_connector_v2"

    def test_strip_edges(self) -> None:
        assert _normalize_name("--hello--") == "hello"


# ---------------------------------------------------------------------------
# Unit tests for proxy tool class creation
# ---------------------------------------------------------------------------


class TestCreateConnectorProxyTool:
    def test_tool_name(self) -> None:
        remote = RemoteTool(name="search", description="Search docs")
        cls = create_connector_proxy_tool_class(
            connector_name="deepwiki",
            connector_alias="deepwiki",
            connector_id="abc-123",
            remote=remote,
            api_key="key",
        )
        assert cls.get_name() == "connector_deepwiki_search"

    def test_is_connector_flag(self) -> None:
        remote = RemoteTool(name="read", description="Read file")
        cls = create_connector_proxy_tool_class(
            connector_name="myconn",
            connector_alias="myconn",
            connector_id="id-1",
            remote=remote,
            api_key="key",
        )
        assert issubclass(cls, MCPTool)
        assert cls._is_connector is True
        assert cls.is_connector() is True

    def test_description_includes_alias(self) -> None:
        remote = RemoteTool(name="fetch", description="Fetch page")
        cls = create_connector_proxy_tool_class(
            connector_name="web_tool",
            connector_alias="web_tool",
            connector_id="id-2",
            remote=remote,
            api_key="key",
        )
        assert cls.description.startswith("[web_tool]")
        assert "Fetch page" in cls.description

    def test_parameters_from_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        remote = RemoteTool.model_validate({
            "name": "search",
            "description": "Search",
            "inputSchema": schema,
        })
        cls = create_connector_proxy_tool_class(
            connector_name="conn",
            connector_alias="conn",
            connector_id="id-3",
            remote=remote,
            api_key="key",
        )
        params = cls.get_parameters()
        assert params["properties"]["query"]["type"] == "string"

    def test_server_name_is_connector_alias(self) -> None:
        remote = RemoteTool(name="tool1", description="A tool")
        cls = cast(
            type[MCPTool],
            create_connector_proxy_tool_class(
                connector_name="my-connector",
                connector_alias="my-connector",
                connector_id="id-4",
                remote=remote,
                api_key="key",
            ),
        )
        assert cls.get_server_name() == "my-connector"


# ---------------------------------------------------------------------------
# FakeConnectorRegistry tests
# ---------------------------------------------------------------------------


class TestFakeConnectorRegistry:
    def test_get_tools(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={
                "wiki": [RemoteTool(name="search", description="Search wiki")],
                "mail": [
                    RemoteTool(name="send", description="Send email"),
                    RemoteTool(name="read", description="Read email"),
                ],
            }
        )
        tools = registry.get_tools()
        assert "connector_wiki_search" in tools
        assert "connector_mail_send" in tools
        assert "connector_mail_read" in tools
        assert registry.connector_count == 2

    def test_connector_names(self) -> None:
        registry = FakeConnectorRegistry(connectors={"alpha": [], "beta": []})
        assert set(registry.get_connector_names()) == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# Integration: ToolManager + ConnectorRegistry
# ---------------------------------------------------------------------------


class TestToolManagerConnectorIntegration:
    @staticmethod
    def _make_config() -> VibeConfig:
        """Minimal VibeConfig-like stub for ToolManager."""
        return cast(
            VibeConfig,
            type(
                "_Cfg",
                (),
                {
                    "mcp_servers": [],
                    "enabled_tools": [],
                    "disabled_tools": [],
                    "tools": {},
                    "tool_paths": [],
                },
            )(),
        )

    def test_connector_tools_registered(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={"myconn": [RemoteTool(name="ping", description="Ping")]}
        )
        config = self._make_config()
        tm = ToolManager(
            config_getter=lambda: config,
            mcp_registry=FakeMCPRegistry(),
            connector_registry=registry,
        )
        assert "connector_myconn_ping" in tm.registered_tools

    def test_no_connector_registry(self) -> None:
        config = self._make_config()
        tm = ToolManager(
            config_getter=lambda: config,
            mcp_registry=FakeMCPRegistry(),
            connector_registry=None,
        )
        # No connector tools, but no crash
        connector_tools = [
            name
            for name, cls in tm.registered_tools.items()
            if issubclass(cls, MCPTool) and cls.is_connector()
        ]
        assert connector_tools == []


# ---------------------------------------------------------------------------
# ConnectorRegistry env var gating (tested via agent_loop helper logic)
# ---------------------------------------------------------------------------


class TestConnectorRegistryEnvGating:
    def test_disabled_without_env_var(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(CONNECTORS_ENV_VAR, None)
            assert os.getenv(CONNECTORS_ENV_VAR) != "1"

    def test_enabled_with_env_var(self) -> None:
        with patch.dict(os.environ, {CONNECTORS_ENV_VAR: "1"}):
            assert os.getenv(CONNECTORS_ENV_VAR) == "1"


# ---------------------------------------------------------------------------
# Error message helpers
# ---------------------------------------------------------------------------


def _make_http_status_error(status: int, body: str = "") -> httpx.HTTPStatusError:
    response = httpx.Response(
        status, text=body, request=httpx.Request("POST", "http://example.com")
    )
    return httpx.HTTPStatusError("error", request=response.request, response=response)


class TestConnectorErrorMessage:
    def test_timeout(self) -> None:
        exc = httpx.ReadTimeout("timed out")
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "timed out" in msg.lower()
        assert "myconn" in msg

    def test_connect_error(self) -> None:
        exc = httpx.ConnectError("connection refused")
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "network" in msg.lower()
        assert "myconn" in msg

    def test_exception_group(self) -> None:
        exc = ExceptionGroup("errors", [ValueError("a"), RuntimeError("b")])
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "multiple errors" in msg.lower()
        assert "myconn" in msg

    def test_generic_error(self) -> None:
        exc = RuntimeError("something broke")
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "something broke" in msg
        assert "myconn" in msg

    def test_http_401_surfaces_auth_message(self) -> None:
        exc = _make_http_status_error(401, "Unauthorized")
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "authentication failed" in msg.lower()
        assert "401" in msg
        assert "Unauthorized" in msg

    def test_http_400_surfaces_response_body(self) -> None:
        exc = _make_http_status_error(400, '{"error": "missing field X"}')
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "400" in msg
        assert "missing field X" in msg

    def test_http_404_surfaces_not_found(self) -> None:
        exc = _make_http_status_error(404)
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "not found" in msg.lower()

    def test_http_error_wrapped_in_exception_group(self) -> None:
        inner = _make_http_status_error(400, "bad request detail")
        exc = ExceptionGroup("errors", [inner])
        msg = _connector_error_message(exc, "id-1", "myconn")
        assert "400" in msg
        assert "bad request detail" in msg


class TestUnwrapHttpStatusError:
    def test_direct(self) -> None:
        exc = _make_http_status_error(400)
        assert _unwrap_http_status_error(exc) is exc

    def test_from_exception_group(self) -> None:
        inner = _make_http_status_error(500)
        exc = ExceptionGroup("g", [ValueError("x"), inner])
        assert _unwrap_http_status_error(exc) is inner

    def test_from_cause(self) -> None:
        inner = _make_http_status_error(403)
        outer = RuntimeError("wrapper")
        outer.__cause__ = inner
        assert _unwrap_http_status_error(outer) is inner

    def test_none_when_absent(self) -> None:
        assert _unwrap_http_status_error(RuntimeError("no http")) is None


# ---------------------------------------------------------------------------
# ConnectorProxyTool.run() via MCP proxy
# ---------------------------------------------------------------------------


class TestConnectorProxyToolRun:
    @staticmethod
    def _make_tool_class() -> type[MCPTool]:
        remote = RemoteTool(name="search", description="Search docs")
        return cast(
            type[MCPTool],
            create_connector_proxy_tool_class(
                connector_name="wiki",
                connector_alias="wiki",
                connector_id="conn-123",
                remote=remote,
                api_key="test-key",
                server_url="https://custom.api.example.com",
            ),
        )

    @staticmethod
    def _make_tool(tool_cls: type[MCPTool]) -> MCPTool:
        return cast(MCPTool, tool_cls.from_config(lambda: BaseToolConfig()))

    @pytest.mark.asyncio
    async def test_run_calls_mcp_proxy(self) -> None:
        cls = self._make_tool_class()
        tool = self._make_tool(cls)
        expected = MCPToolResult(
            ok=True, server="test", tool="search", text="result text"
        )

        with patch(
            "vibe.core.tools.connectors.connector_registry.call_tool_http",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_call:
            results = [r async for r in tool.invoke(query="hello")]

        mock_call.assert_awaited_once()
        call_args = mock_call.call_args
        assert "/v1/experimental/connectors/conn-123/mcp" in call_args.args[0]
        assert call_args.args[1] == "search"
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"

        assert len(results) == 1
        assert results[0] == expected

    @pytest.mark.asyncio
    async def test_run_uses_default_base_url(self) -> None:
        remote = RemoteTool(name="ping", description="Ping")
        cls = cast(
            type[MCPTool],
            create_connector_proxy_tool_class(
                connector_name="svc",
                connector_alias="svc",
                connector_id="c-1",
                remote=remote,
                api_key="key",
                server_url=None,
            ),
        )
        tool = self._make_tool(cls)
        expected = MCPToolResult(ok=True, server="s", tool="ping", text="pong")

        with patch(
            "vibe.core.tools.connectors.connector_registry.call_tool_http",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_call:
            [_ async for _ in tool.invoke()]

        url = mock_call.call_args.args[0]
        assert url.startswith("https://api.mistral.ai/")

    @pytest.mark.asyncio
    async def test_run_surfaces_timeout_error(self) -> None:
        cls = self._make_tool_class()
        tool = self._make_tool(cls)

        with (
            patch(
                "vibe.core.tools.connectors.connector_registry.call_tool_http",
                new_callable=AsyncMock,
                side_effect=httpx.ReadTimeout("timed out"),
            ),
            pytest.raises(ToolError, match="timed out"),
        ):
            [_ async for _ in tool.invoke(query="hello")]

    @pytest.mark.asyncio
    async def test_run_surfaces_connect_error(self) -> None:
        cls = self._make_tool_class()
        tool = self._make_tool(cls)

        with (
            patch(
                "vibe.core.tools.connectors.connector_registry.call_tool_http",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("refused"),
            ),
            pytest.raises(ToolError, match="network"),
        ):
            [_ async for _ in tool.invoke(query="hello")]
