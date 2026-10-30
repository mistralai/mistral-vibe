from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import patch

import httpx
import httpx2
import pytest
import respx

from tests.conftest import build_test_vibe_config
from tests.constants import CONNECTORS_BOOTSTRAP_PATH, MISTRAL_BASE_URL
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.config import ConnectorConfig, VibeConfigSchema
from vibe.core.tools.connectors import (
    compute_connector_counts,
    connector_registry as connector_registry_module,
)
from vibe.core.tools.connectors.connector_registry import (
    _BOOTSTRAP_CACHE_TTL_SECONDS,
    ConnectorAuthAction,
    ConnectorRegistry,
    RemoteTool,
    _bootstrap_cache_key,
    _normalize_name,
    _unwrap_http_status_error,
    builtin_connectors,
    create_connector_proxy_tool_class,
)
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.mcp.tools import MCPTool

_BOOTSTRAP_CACHE_FILE_NAME = "connector_bootstrap_cache.json"

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
            connector_alias="deepwiki", remote=remote
        )
        assert cls.get_name() == "connector_deepwiki_search"

    def test_is_connector_flag(self) -> None:
        remote = RemoteTool(name="read", description="Read file")
        cls = create_connector_proxy_tool_class(connector_alias="myconn", remote=remote)
        assert issubclass(cls, MCPTool)
        assert cls._is_connector is True
        assert cls.is_connector() is True

    def test_description_includes_alias(self) -> None:
        remote = RemoteTool(name="fetch", description="Fetch page")
        cls = create_connector_proxy_tool_class(
            connector_alias="web_tool", remote=remote
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
        cls = create_connector_proxy_tool_class(connector_alias="conn", remote=remote)
        params = cls.get_parameters()
        assert params["properties"]["query"]["type"] == "string"

    def test_server_name_is_connector_alias(self) -> None:
        remote = RemoteTool(name="tool1", description="A tool")
        cls = cast(
            type[MCPTool],
            create_connector_proxy_tool_class(
                connector_alias="my-connector", remote=remote
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
    def _make_config(
        connectors: list[ConnectorConfig] | None = None,
    ) -> VibeConfigSchema:
        return build_test_vibe_config(connectors=connectors or [])

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
# Error message helpers
# ---------------------------------------------------------------------------


def _make_http_status_error(status: int, body: str = "") -> httpx2.HTTPStatusError:
    response = httpx2.Response(
        status, text=body, request=httpx2.Request("POST", "http://example.com")
    )
    return httpx2.HTTPStatusError("error", request=response.request, response=response)


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


class TestConnectorDisableFiltering:
    @staticmethod
    def _make_config(
        connectors: list[ConnectorConfig] | None = None,
    ) -> VibeConfigSchema:
        return build_test_vibe_config(connectors=connectors or [])

    def test_disabled_connector_excludes_all_tools(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={
                "wiki": [
                    RemoteTool(name="search", description="Search"),
                    RemoteTool(name="read", description="Read"),
                ]
            }
        )
        config = self._make_config(
            connectors=[ConnectorConfig(name="wiki", disabled=True)]
        )
        tm = ToolManager(
            config_getter=lambda: config,
            mcp_registry=FakeMCPRegistry(),
            connector_registry=registry,
        )
        assert "connector_wiki_search" not in tm.available_tools
        assert "connector_wiki_read" not in tm.available_tools
        # But still registered (discoverable for UI)
        assert "connector_wiki_search" in tm.registered_tools

    def test_disabled_tools_filters_specific_tools(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={
                "mail": [
                    RemoteTool(name="send", description="Send"),
                    RemoteTool(name="read", description="Read"),
                ]
            }
        )
        config = self._make_config(
            connectors=[ConnectorConfig(name="mail", disabled_tools=["send"])]
        )
        tm = ToolManager(
            config_getter=lambda: config,
            mcp_registry=FakeMCPRegistry(),
            connector_registry=registry,
        )
        assert "connector_mail_send" not in tm.available_tools
        assert "connector_mail_read" in tm.available_tools

    def test_no_config_means_all_disabled_by_default(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={"wiki": [RemoteTool(name="search", description="Search")]}
        )
        config = self._make_config(connectors=[])
        tm = ToolManager(
            config_getter=lambda: config,
            mcp_registry=FakeMCPRegistry(),
            connector_registry=registry,
        )
        # Connectors without config entries are disabled by default
        assert "connector_wiki_search" not in tm.available_tools
        # But still registered (discoverable for UI)
        assert "connector_wiki_search" in tm.registered_tools

    def test_unrelated_config_does_not_affect_other_connectors(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={
                "wiki": [RemoteTool(name="search", description="Search")],
                "mail": [RemoteTool(name="send", description="Send")],
            }
        )
        # Explicitly enable wiki, disable mail
        config = self._make_config(
            connectors=[
                ConnectorConfig(name="mail", disabled=True),
                ConnectorConfig(name="wiki", disabled=False),
            ]
        )
        tm = ToolManager(
            config_getter=lambda: config,
            mcp_registry=FakeMCPRegistry(),
            connector_registry=registry,
        )
        assert "connector_wiki_search" in tm.available_tools
        assert "connector_mail_send" not in tm.available_tools


# ---------------------------------------------------------------------------
# Bootstrap-based discovery (ConnectorRegistry._discover_all via httpx)
# ---------------------------------------------------------------------------

_BOOTSTRAP_URL = f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}"


def _make_bootstrap_response(
    connectors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {"connectors": connectors or [], "errors": None}


def _make_connector_payload(
    *,
    connector_id: str = "conn-1",
    name: str = "wiki",
    protocol: str = "mcp",
    is_ready: bool = True,
    tools: list[dict[str, Any]] | None = None,
    bootstrap_errors: list[str] | None = None,
    auth_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": connector_id,
        "name": name,
        "protocol": protocol,
        "display_name": name,
        "description": name,
        "status": {"is_ready": is_ready},
        "tools": tools or [],
        "bootstrap_errors": bootstrap_errors,
        "auth_action": auth_action,
    }


def _make_tool_payload(
    name: str = "search", description: str = "Search docs"
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }


class TestBootstrapDiscovery:
    @respx.mock
    @pytest.mark.asyncio
    async def test_discovers_tools_from_bootstrap(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="wiki",
                tools=[_make_tool_payload("search"), _make_tool_payload("read")],
            )
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert "connector_wiki_search" in tools
        assert "connector_wiki_read" in tools
        assert registry.connector_count == 1
        assert registry.is_connected("wiki")

    @respx.mock
    @pytest.mark.asyncio
    async def test_skips_not_ready_connectors(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="broken", is_ready=False, bootstrap_errors=["auth failed"]
            ),
            _make_connector_payload(name="healthy", tools=[_make_tool_payload("ping")]),
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert "connector_healthy_ping" in tools
        assert not any("broken" in name for name in tools)
        assert not registry.is_connected("broken")
        assert registry.is_connected("healthy")

    @respx.mock
    @pytest.mark.asyncio
    async def test_trusts_server_capability_filter(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="shared", protocol="http", tools=[_make_tool_payload("request")]
            ),
            _make_connector_payload(
                name="shared", tools=[_make_tool_payload("search")]
            ),
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert "connector_shared_request" in tools
        assert "connector_shared_2_search" in tools
        assert registry.get_connector_names() == ["shared", "shared_2"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_handles_bootstrap_http_error(self) -> None:
        respx.get(_BOOTSTRAP_URL).mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert tools == {}
        assert registry.connector_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_bootstrap_http_error_records_bootstrap_error(self) -> None:
        respx.get(_BOOTSTRAP_URL).mock(
            return_value=httpx.Response(502, text="Bad Gateway")
        )

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        error = registry.bootstrap_error()
        assert error is not None
        assert "502" in error

    @respx.mock
    @pytest.mark.asyncio
    async def test_failed_bootstrap_retries_on_next_get_tools(self) -> None:
        # A failed bootstrap must not be cached, so the next integrate (e.g.
        # triggered by `/reload`) actually re-hits the endpoint.
        route = respx.get(_BOOTSTRAP_URL).mock(
            side_effect=[
                httpx.Response(502, text="Bad Gateway"),
                httpx.Response(
                    200,
                    json=_make_bootstrap_response([
                        _make_connector_payload(
                            name="wiki", tools=[_make_tool_payload("search")]
                        )
                    ]),
                ),
            ]
        )

        registry = ConnectorRegistry(api_key="test-key")
        first = await registry.get_tools_async()
        assert first == {}
        assert registry.bootstrap_error() is not None

        second = await registry.get_tools_async()
        assert "connector_wiki_search" in second
        assert registry.bootstrap_error() is None
        assert route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_bootstrap_5xx_error_suggests_reload(self) -> None:
        respx.get(_BOOTSTRAP_URL).mock(
            return_value=httpx.Response(502, text="Bad Gateway")
        )

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        error = registry.bootstrap_error() or ""
        assert "502" in error
        assert "/reload" in error

    @respx.mock
    @pytest.mark.asyncio
    async def test_bootstrap_4xx_error_omits_reload(self) -> None:
        respx.get(_BOOTSTRAP_URL).mock(
            return_value=httpx.Response(403, text="Forbidden")
        )

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        error = registry.bootstrap_error() or ""
        assert "403" in error
        assert "/reload" not in error

    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_bootstrap_has_no_error(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("search")])
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        assert registry.bootstrap_error() is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_per_connector_bootstrap_error_is_recorded(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                connector_id="conn-slack",
                name="slack",
                is_ready=False,
                bootstrap_errors=["Slack OAuth token expired"],
            ),
            _make_connector_payload(
                connector_id="conn-wiki",
                name="wiki",
                tools=[_make_tool_payload("search")],
            ),
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        slack_error = registry.connector_error_for("slack")
        assert slack_error is not None
        assert "Slack OAuth token expired" in slack_error
        assert registry.connector_error_for("wiki") is None
        # The endpoint itself succeeded, so there is no whole-request error.
        assert registry.bootstrap_error() is None

    @pytest.mark.asyncio
    async def test_fresh_bootstrap_cache_avoids_http(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("cached")])
        ])

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_000),
            respx.mock,
        ):
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=payload)
            )
            await ConnectorRegistry(api_key="test-key").get_tools_async()

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_100),
            respx.mock(assert_all_called=False) as router,
        ):
            route = router.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(500, text="should not be called")
            )
            tools = await ConnectorRegistry(api_key="test-key").get_tools_async()

        assert "connector_wiki_cached" in tools
        assert not route.called

    @pytest.mark.asyncio
    async def test_bootstrap_cache_without_protocol_uses_legacy_mcp(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        cached_payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("cached")])
        ])
        with (
            patch.object(connector_registry_module.time, "time", return_value=1_000),
            respx.mock,
        ):
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=cached_payload)
            )
            await ConnectorRegistry(api_key="test-key").get_tools_async()

        cache_path = tmp_path / _BOOTSTRAP_CACHE_FILE_NAME
        cache = json.loads(cache_path.read_text())
        next(iter(cache.values()))["payload"]["connectors"][0].pop("protocol")
        cache_path.write_text(json.dumps(cache))

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_100),
            respx.mock,
        ):
            route = respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(500, text="should not be called")
            )
            tools = await ConnectorRegistry(api_key="test-key").get_tools_async()

        assert not route.called
        assert "connector_wiki_cached" in tools

    @pytest.mark.asyncio
    async def test_stale_bootstrap_cache_falls_back_to_http(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        cached_payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("cached")])
        ])
        fresh_payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("fresh")])
        ])

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_000),
            respx.mock,
        ):
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=cached_payload)
            )
            await ConnectorRegistry(api_key="test-key").get_tools_async()

        with (
            patch.object(
                connector_registry_module.time,
                "time",
                return_value=1_000 + _BOOTSTRAP_CACHE_TTL_SECONDS + 1,
            ),
            respx.mock,
        ):
            route = respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=fresh_payload)
            )
            tools = await ConnectorRegistry(api_key="test-key").get_tools_async()

        assert route.called
        assert "connector_wiki_fresh" in tools
        assert "connector_wiki_cached" not in tools

    @pytest.mark.asyncio
    async def test_malformed_bootstrap_cache_falls_back_to_http(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        (tmp_path / _BOOTSTRAP_CACHE_FILE_NAME).write_text("{bad toml")
        payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("fresh")])
        ])

        with respx.mock:
            route = respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=payload)
            )
            tools = await ConnectorRegistry(api_key="test-key").get_tools_async()

        assert route.called
        assert "connector_wiki_fresh" in tools

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_fresh_bootstrap_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        cached_payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("cached")])
        ])
        fresh_payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("fresh")])
        ])

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_000),
            respx.mock,
        ):
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=cached_payload)
            )
            await ConnectorRegistry(api_key="test-key").get_tools_async()

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_100),
            respx.mock,
        ):
            route = respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=fresh_payload)
            )
            tools = await ConnectorRegistry(api_key="test-key").get_tools_async(
                force_refresh=True
            )

        assert route.called
        assert "connector_wiki_fresh" in tools
        assert "connector_wiki_cached" not in tools

    @pytest.mark.asyncio
    async def test_bootstrap_cache_does_not_store_raw_api_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("cached")])
        ])

        with respx.mock:
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=payload)
            )
            await ConnectorRegistry(api_key="secret-test-key").get_tools_async()

        cache_text = (tmp_path / _BOOTSTRAP_CACHE_FILE_NAME).read_text()
        assert "secret-test-key" not in cache_text

    @pytest.mark.asyncio
    async def test_bootstrap_cache_stores_only_consumed_connector_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        payload = _make_bootstrap_response([
            _make_connector_payload(
                tools=[{**_make_tool_payload("cached"), "secret_extra": "tool-secret"}],
                auth_action={"type": "oauth", "url": "https://secret.example.com"},
            )
            | {
                "display_name": "Private display name",
                "description": "Private connector description",
                "bootstrap_errors": ["private error detail"],
            }
        ])

        with respx.mock:
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=payload)
            )
            await ConnectorRegistry(api_key="test-key").get_tools_async()

        cache_text = (tmp_path / _BOOTSTRAP_CACHE_FILE_NAME).read_text()
        assert "Private display name" not in cache_text
        assert "Private connector description" not in cache_text
        assert "https://secret.example.com" not in cache_text
        assert "tool-secret" not in cache_text

        cache = json.loads(cache_text)
        entry = next(iter(cache.values()))
        connector = entry["payload"]["connectors"][0]
        # bootstrap_errors is a consumed field (surfaced to the user), so it is
        # persisted; a warm cache hit must rebuild the per-connector error.
        assert set(connector) == {
            "id",
            "name",
            "protocol",
            "status",
            "tools",
            "auth_action",
            "bootstrap_errors",
        }
        assert connector["auth_action"] == {"type": "oauth"}
        assert connector["bootstrap_errors"] == ["private error detail"]
        assert set(connector["tools"][0]) == {"name", "description", "inputSchema"}

    @pytest.mark.asyncio
    async def test_fresh_bootstrap_cache_preserves_connector_errors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="slack",
                is_ready=False,
                bootstrap_errors=["Slack OAuth token expired"],
            )
        ])

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_000),
            respx.mock,
        ):
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=payload)
            )
            first = ConnectorRegistry(api_key="test-key")
            await first.get_tools_async()
            assert "Slack OAuth token expired" in (
                first.connector_error_for("slack") or ""
            )

        with (
            patch.object(connector_registry_module.time, "time", return_value=1_100),
            respx.mock(assert_all_called=False) as router,
        ):
            route = router.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(500, text="should not be called")
            )
            second = ConnectorRegistry(api_key="test-key")
            await second.get_tools_async()

        assert not route.called
        assert second.connector_error_for("slack") == "Slack OAuth token expired"

    @pytest.mark.asyncio
    async def test_bootstrap_cache_uses_dedicated_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("cached")])
        ])

        with respx.mock:
            respx.get(_BOOTSTRAP_URL).mock(
                return_value=httpx.Response(200, json=payload)
            )
            await ConnectorRegistry(api_key="test-key").get_tools_async()

        assert (tmp_path / _BOOTSTRAP_CACHE_FILE_NAME).exists()
        assert not (tmp_path / "cache.toml").exists()

    @respx.mock
    @pytest.mark.asyncio
    async def test_deduplicates_connector_aliases(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                connector_id="c-1", name="mcp", tools=[_make_tool_payload("a")]
            ),
            _make_connector_payload(
                connector_id="c-2", name="mcp", tools=[_make_tool_payload("b")]
            ),
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert "connector_mcp_a" in tools
        assert "connector_mcp_2_b" in tools
        assert registry.connector_count == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_skips_connector_without_id(self) -> None:
        payload = _make_bootstrap_response([
            {"name": "broken", "status": {"is_ready": True}, "tools": []},
            _make_connector_payload(name="valid", tools=[_make_tool_payload("ping")]),
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert "connector_valid_ping" in tools
        assert registry.connector_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_connectors_list(self) -> None:
        payload = _make_bootstrap_response([])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert tools == {}
        assert registry.connector_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_uses_custom_server_url(self) -> None:
        custom_url = "https://custom.api.example.com/v1/connectors/bootstrap"
        payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("ping")])
        ])
        respx.get(custom_url).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(
            api_key="test-key", server_url="https://custom.api.example.com"
        )
        tools = await registry.get_tools_async()

        assert "connector_wiki_ping" in tools

    @respx.mock
    @pytest.mark.asyncio
    async def test_caches_after_first_call(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(tools=[_make_tool_payload("search")])
        ])
        route = respx.get(_BOOTSTRAP_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()
        await registry.get_tools_async()

        assert route.call_count == 1


# ---------------------------------------------------------------------------
# Auth-actionable connector discovery
# ---------------------------------------------------------------------------


class TestAuthActionablediscovery:
    @respx.mock
    @pytest.mark.asyncio
    async def test_bootstrap_url_opts_into_auth_actionable_connectors(self) -> None:
        payload = _make_bootstrap_response([])
        route = respx.get(_BOOTSTRAP_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        registry = ConnectorRegistry(
            api_key="test-key",
            builtins=builtin_connectors(enable_document_library_connector=True),
        )
        await registry.get_tools_async()

        assert route.called
        called_url = str(route.calls.last.request.url)
        assert "include_auth_actionable_connectors=true" in called_url
        # Repeated query param, not a comma-joined value.
        assert "builtin_connectors=web_search" in called_url
        assert "builtin_connectors=document_library" in called_url
        assert "web_search%2Cdocument_library" not in called_url
        assert "supports_mcp=true" in called_url

    @respx.mock
    @pytest.mark.asyncio
    async def test_document_library_withheld_when_connector_disabled(self) -> None:
        payload = _make_bootstrap_response([])
        route = respx.get(_BOOTSTRAP_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        registry = ConnectorRegistry(
            api_key="test-key",
            builtins=builtin_connectors(enable_document_library_connector=False),
        )
        await registry.get_tools_async()

        assert route.called
        called_url = str(route.calls.last.request.url)
        assert "builtin_connectors=web_search" in called_url
        assert "builtin_connectors=document_library" not in called_url

    @respx.mock
    @pytest.mark.asyncio
    async def test_oauth_connector_is_discovered_but_disconnected(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="linear", is_ready=False, tools=[], auth_action={"type": "oauth"}
            )
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        tools = await registry.get_tools_async()

        assert tools == {}
        assert "linear" in registry.get_connector_names()
        assert not registry.is_connected("linear")
        assert registry.get_auth_action("linear") == ConnectorAuthAction.OAUTH

    @respx.mock
    @pytest.mark.asyncio
    async def test_credentials_setup_connector_is_discovered_but_disconnected(
        self,
    ) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="custom_crm",
                is_ready=False,
                tools=[],
                auth_action={"type": "credentials_setup"},
            )
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        assert "custom_crm" in registry.get_connector_names()
        assert not registry.is_connected("custom_crm")
        assert (
            registry.get_auth_action("custom_crm")
            == ConnectorAuthAction.CREDENTIALS_SETUP
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_ready_connector_has_no_auth_action(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(name="wiki", tools=[_make_tool_payload("search")])
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        assert registry.is_connected("wiki")
        assert registry.get_auth_action("wiki") == ConnectorAuthAction.NONE

    @respx.mock
    @pytest.mark.asyncio
    async def test_degraded_connector_has_no_auth_action(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="degraded",
                is_ready=False,
                tools=[],
                bootstrap_errors=["tools_or_system_prompt_failed: timeout"],
                auth_action=None,
            )
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        assert "degraded" in registry.get_connector_names()
        assert not registry.is_connected("degraded")
        assert registry.get_auth_action("degraded") == ConnectorAuthAction.NONE

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_auth_action_type_is_treated_as_none(self) -> None:
        payload = _make_bootstrap_response([
            _make_connector_payload(
                name="weird",
                is_ready=False,
                tools=[],
                auth_action={"type": "magic_link"},
            )
        ])
        respx.get(_BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json=payload))

        registry = ConnectorRegistry(api_key="test-key")
        await registry.get_tools_async()

        assert registry.get_auth_action("weird") == ConnectorAuthAction.NONE

    def test_get_auth_action_unknown_alias_returns_none(self) -> None:
        registry = ConnectorRegistry(api_key="test-key")
        assert registry.get_auth_action("nobody") == ConnectorAuthAction.NONE


def test_builtin_connectors_gates_document_library() -> None:
    assert builtin_connectors(enable_document_library_connector=False) == (
        "web_search",
    )
    assert builtin_connectors(enable_document_library_connector=True) == (
        "web_search",
        "document_library",
    )


def test_bootstrap_cache_key_folds_builtins() -> None:
    # Different builtins must produce different cache keys so the two flag states
    # never read each other's cached catalog from disk.
    disabled = _bootstrap_cache_key(
        "test-key", None, builtin_connectors(enable_document_library_connector=False)
    )
    enabled = _bootstrap_cache_key(
        "test-key", None, builtin_connectors(enable_document_library_connector=True)
    )
    assert disabled != enabled
    # Same builtins -> stable, reproducible key.
    assert disabled == _bootstrap_cache_key(
        "test-key", None, builtin_connectors(enable_document_library_connector=False)
    )


# ---------------------------------------------------------------------------
# Banner connector counts
# ---------------------------------------------------------------------------


class TestComputeConnectorCounts:
    def test_no_registry(self) -> None:
        assert compute_connector_counts(build_test_vibe_config(), None) == (0, None)

    def test_empty_registry(self) -> None:
        registry = FakeConnectorRegistry()
        assert compute_connector_counts(build_test_vibe_config(), registry) == (0, 0)

    def test_no_config_entry_is_disabled_by_default(self) -> None:
        # Mirrors ToolManager: connectors without an explicit ConnectorConfig
        # entry are disabled (their tools are not registered).
        registry = FakeConnectorRegistry({
            "alpha": [RemoteTool(name="search")],
            "beta": [RemoteTool(name="list")],
        })
        assert compute_connector_counts(build_test_vibe_config(), registry) == (0, 2)

    def test_explicitly_enabled_and_connected(self) -> None:
        registry = FakeConnectorRegistry({
            "alpha": [RemoteTool(name="search")],
            "beta": [RemoteTool(name="list")],
        })
        config = build_test_vibe_config(
            connectors=[
                ConnectorConfig(name="alpha", disabled=False),
                ConnectorConfig(name="beta", disabled=False),
            ]
        )
        assert compute_connector_counts(config, registry) == (2, 2)

    def test_auth_pending_not_counted(self) -> None:
        # Empty tool list → FakeConnectorRegistry marks as not connected.
        registry = FakeConnectorRegistry(
            {"alpha": [RemoteTool(name="search")], "needs_auth": []},
            auth_actions={"needs_auth": ConnectorAuthAction.OAUTH},
        )
        config = build_test_vibe_config(
            connectors=[
                ConnectorConfig(name="alpha", disabled=False),
                ConnectorConfig(name="needs_auth", disabled=False),
            ]
        )
        assert compute_connector_counts(config, registry) == (1, 2)

    def test_disabled_not_counted(self) -> None:
        registry = FakeConnectorRegistry({
            "alpha": [RemoteTool(name="search")],
            "beta": [RemoteTool(name="list")],
        })
        config = build_test_vibe_config(
            connectors=[
                ConnectorConfig(name="alpha", disabled=False),
                ConnectorConfig(name="beta", disabled=True),
            ]
        )
        assert compute_connector_counts(config, registry) == (1, 2)

    def test_disabled_and_pending_combined(self) -> None:
        registry = FakeConnectorRegistry(
            {
                "alpha": [RemoteTool(name="search")],
                "beta": [RemoteTool(name="list")],
                "needs_auth": [],
            },
            auth_actions={"needs_auth": ConnectorAuthAction.OAUTH},
        )
        config = build_test_vibe_config(
            connectors=[
                ConnectorConfig(name="alpha", disabled=False),
                ConnectorConfig(name="beta", disabled=True),
                ConnectorConfig(name="needs_auth", disabled=False),
            ]
        )
        assert compute_connector_counts(config, registry) == (1, 3)
