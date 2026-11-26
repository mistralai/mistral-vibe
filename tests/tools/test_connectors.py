from __future__ import annotations

from typing import cast

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.core.config import ConnectorConfig, VibeConfigSchema
from vibe.core.tools.connectors import compute_connector_counts
from vibe.core.tools.connectors.connector_registry import (
    ConnectorAuthAction,
    ConnectorCatalogEntry,
    ConnectorRegistry,
    ConnectorToolDefinition,
    RemoteTool,
    _normalize_name,
    builtin_connectors,
    create_connector_proxy_tool_class,
)
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.mcp.tools import MCPTool

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


def test_builtin_connectors_gates_document_library() -> None:
    assert builtin_connectors(
        enable_document_library_connector=False, enable_mistral_mcp_connector=False
    ) == ("web_search",)
    assert builtin_connectors(
        enable_document_library_connector=True, enable_mistral_mcp_connector=False
    ) == ("web_search", "document_library")


def test_builtin_connectors_gates_mistral_mcp() -> None:
    assert "mistral_mcp" not in builtin_connectors(
        enable_document_library_connector=False, enable_mistral_mcp_connector=False
    )
    assert builtin_connectors(
        enable_document_library_connector=False, enable_mistral_mcp_connector=True
    ) == ("web_search", "mistral_mcp")


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


def test_the_registry_serves_the_catalog_the_app_server_hands_it() -> None:
    """*Prepare*: A catalog with a ready connector and one that needs OAuth.
    *Do*: Build the registry from it.
    *Assert*: The ready connector's tools are served and the other reads as
      needing authorization; the registry fetches nothing of its own.
    """
    # Prepare
    entries = (
        ConnectorCatalogEntry(
            connector_id="raw-wiki",
            alias="wiki",
            display_name="Wiki",
            ready=True,
            auth_action=ConnectorAuthAction.NONE,
            tools=(ConnectorToolDefinition("search", "Search", {"type": "object"}),),
        ),
        ConnectorCatalogEntry(
            connector_id="raw-mail",
            alias="mail",
            display_name="Mail",
            ready=False,
            auth_action=ConnectorAuthAction.OAUTH,
            tools=(),
        ),
    )

    # Do
    registry = ConnectorRegistry(entries)

    # Assert
    assert set(registry.get_tools()) == {"connector_wiki_search"}
    assert registry.get_connector_names() == ["wiki", "mail"]
    assert not registry.is_connected("mail")
    assert registry.get_auth_action("mail") is ConnectorAuthAction.OAUTH
