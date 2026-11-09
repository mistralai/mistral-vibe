from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._plugin_mcp import (
    PluginMCPServerEntry,
    PluginMCPSource,
    PluginMCPStatus,
    PluginMCPTool,
)
from vibe.app_server._session_backend_port import (
    SessionMCPSourceState,
    SessionMCPState,
    SessionMCPToolDescriptor,
)
from vibe.app_server.mcp_catalog import MCPCatalogService, project_mcp_sources
from vibe.app_server.models import MCPSourceStatus
from vibe.core.config import MCPHttp, MCPStdio
from vibe.core.plugins import PluginMCPServerDefinition


def test_project_mcp_sources_includes_disabled_server_without_discovery() -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(name="local", transport="stdio", command="fake-mcp", disabled=True)
        ]
    )

    sources, discovery_errors = project_mcp_sources(
        orchestrator=FakeConfigOrchestrator(config),
        state=SessionMCPState(
            catalog_revision="catalog",
            route_revision="route",
            sources=(),
            discovery_errors={},
        ),
    )

    assert [(source.name, source.status) for source in sources] == [
        ("local", MCPSourceStatus.DISABLED)
    ]
    assert discovery_errors == {}


def _plugin_source(
    source_id: str,
    *,
    status: PluginMCPStatus = "needs_auth",
    tools: tuple[PluginMCPTool, ...] = (),
) -> PluginMCPSource:
    return PluginMCPSource(
        entry=PluginMCPServerEntry(
            definition=PluginMCPServerDefinition(
                plugin_name="linear-probe",
                plugin_namespace="linear-probe",
                source_id=source_id,
                private_alias=f"plugin_probe_{source_id}",
                server=MCPHttp(
                    name=f"plugin_probe_{source_id}",
                    transport="http",
                    url="https://plugin.test/mcp",
                ),
                config_file=Path("/plugins/linear-probe/mcp.json"),
            ),
            server=MCPHttp(
                name=source_id, transport="http", url="https://plugin.test/mcp"
            ),
        ),
        status=status,
        tools=tools,
    )


def test_project_mcp_sources_carries_plugin_servers_into_the_runtime() -> None:
    # Prepare
    config = build_test_vibe_config(
        mcp_servers=[MCPStdio(name="local", transport="stdio", command="fake-mcp")]
    )

    # Do
    sources, _ = project_mcp_sources(
        orchestrator=FakeConfigOrchestrator(config),
        state=SessionMCPState(
            catalog_revision="catalog",
            route_revision="route",
            sources=(),
            discovery_errors={},
        ),
        plugin_sources=[_plugin_source("linear-plugin")],
    )

    # Assert
    assert [(source.name, source.status) for source in sources] == [
        ("local", MCPSourceStatus.UNAVAILABLE),
        ("linear-plugin", MCPSourceStatus.NEEDS_AUTH),
    ]
    assert sources[1].plugin_name == "linear-probe"


def test_project_mcp_sources_drops_a_plugin_server_a_config_name_shadows() -> None:
    # Prepare
    config = build_test_vibe_config(
        mcp_servers=[MCPStdio(name="local", transport="stdio", command="fake-mcp")]
    )

    # Do
    sources, _ = project_mcp_sources(
        orchestrator=FakeConfigOrchestrator(config),
        state=SessionMCPState(
            catalog_revision="catalog",
            route_revision="route",
            sources=(),
            discovery_errors={},
        ),
        plugin_sources=[_plugin_source("local")],
    )

    # Assert
    assert [(source.name, source.plugin_name) for source in sources] == [
        ("local", None)
    ]


def test_a_plugin_tool_description_is_flattened_to_the_row_the_view_renders() -> None:
    # Prepare
    config = build_test_vibe_config(mcp_servers=[])
    source = _plugin_source(
        "linear-plugin",
        status="connected",
        tools=(
            PluginMCPTool(
                name="create_issue",
                description=(
                    "Create an issue.\n\nAccepts a title and a team id.\n- title\n- team"
                ),
            ),
        ),
    )

    # Do
    sources, _ = project_mcp_sources(
        orchestrator=FakeConfigOrchestrator(config),
        state=SessionMCPState(
            catalog_revision="catalog",
            route_revision="route",
            sources=(),
            discovery_errors={},
        ),
        plugin_sources=[source],
    )

    # Assert
    assert [tool.description for tool in sources[0].tools] == ["Create an issue."]


def test_a_plugin_row_reads_the_session_connection_over_app_discovery() -> None:
    # Prepare
    config = build_test_vibe_config(mcp_servers=[])
    state = SessionMCPState(
        catalog_revision="catalog",
        route_revision="route",
        sources=(
            SessionMCPSourceState(
                name="linear-plugin",
                transport="http",
                status="connected",
                tools=(
                    SessionMCPToolDescriptor(
                        remote_name="create_issue",
                        description="Create an issue.",
                        enabled=True,
                        display_name="linear-plugin_create_issue",
                    ),
                ),
            ),
        ),
        discovery_errors={},
    )

    # Do
    sources, _ = project_mcp_sources(
        orchestrator=FakeConfigOrchestrator(config),
        state=state,
        plugin_sources=[_plugin_source("linear-plugin", status="unavailable")],
    )

    # Assert
    assert [(source.name, source.status, source.plugin_name) for source in sources] == [
        ("linear-plugin", MCPSourceStatus.CONNECTED, "linear-probe")
    ]
    assert [tool.name for tool in sources[0].tools] == ["create_issue"]


@pytest.mark.asyncio
async def test_resolve_catalog_disables_servers_when_mcp_is_disabled() -> None:
    """UH path: resolve_catalog marks all servers disabled when enable_mcp is false."""
    config = build_test_vibe_config(
        enable_mcp=False,
        mcp_servers=[MCPStdio(name="local", transport="stdio", command="fake-mcp")],
    )

    catalog = await MCPCatalogService(MCPAuthenticationService()).resolve_catalog(
        FakeConfigOrchestrator(config)
    )

    assert [(server.name, server.disabled) for server in catalog.servers] == [
        ("local", True)
    ]


@pytest.mark.asyncio
async def test_resolve_catalog_keeps_enabled_servers_when_mcp_is_enabled() -> None:
    config = build_test_vibe_config(
        mcp_servers=[MCPStdio(name="local", transport="stdio", command="fake-mcp")]
    )

    catalog = await MCPCatalogService(MCPAuthenticationService()).resolve_catalog(
        FakeConfigOrchestrator(config)
    )

    assert [(server.name, server.disabled) for server in catalog.servers] == [
        ("local", False)
    ]
