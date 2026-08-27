"""Offline stand-ins for the two ports `PluginMaterializer` connects through.

The conformance suite has to produce byte-identical snapshots with no network,
so it drives materialization through doubles. What they answer with mirrors the
shared fixtures exactly — `fixture_mcp_tool` is what `mcp-tools/server.py`
reports over stdio — so the end-to-end test that drives the real server agrees
with the checked-in bytes instead of describing a parallel truth.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from vibe.core.plugins import PluginMCPServerDefinition
from vibe.core.plugins._catalog import (
    PluginMCPAuthorizationRequired,
    PluginMCPDiscoveryError,
)
from vibe.core.tools.connectors.connector_registry import ConnectorToolDefinition
from vibe.core.tools.remote import RemoteTool


def fixture_mcp_tool(server_name: str) -> RemoteTool:
    """The single tool `mcp-tools/server.py` answers `tools/list` with."""
    return RemoteTool.model_validate({
        "name": "lookup",
        "description": f"Look up {server_name} records.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "server": {"type": "string"}},
            "required": ["query", "server"],
        },
    })


FIXTURE_CONNECTOR_TOOLS: Mapping[str, tuple[ConnectorToolDefinition, ...]] = {
    "records": (
        ConnectorToolDefinition(
            name="lookup",
            description="Look up managed records.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    )
}


class FakeMCPDiscovery:
    def __init__(
        self,
        tools: Mapping[str, tuple[RemoteTool, ...]] | None = None,
        *,
        failures: Mapping[str, str] | None = None,
        unauthorized: Iterable[str] = (),
    ) -> None:
        self._tools = tools
        self._failures = failures or {}
        self._unauthorized = frozenset(unauthorized)

    async def discover(
        self, definition: PluginMCPServerDefinition
    ) -> tuple[RemoteTool, ...]:
        if definition.source_id in self._unauthorized:
            raise PluginMCPAuthorizationRequired(definition.source_id)
        failure = self._failures.get(definition.source_id)
        if failure is not None:
            raise PluginMCPDiscoveryError(failure)
        if self._tools is None:
            return (fixture_mcp_tool(definition.source_id),)
        return self._tools[definition.source_id]


class FakeConnectorCatalog:
    def __init__(
        self, tools: Mapping[str, tuple[ConnectorToolDefinition, ...]] | None = None
    ) -> None:
        self._tools = FIXTURE_CONNECTOR_TOOLS if tools is None else tools

    def tools(self, source_id: str) -> tuple[ConnectorToolDefinition, ...] | None:
        return self._tools.get(source_id)
