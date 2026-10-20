"""Connect a plugin's declared sources and turn what they answer into a catalog.

Resolution reads the filesystem and stops. This is the step after: it connects
the MCP servers a plugin declares, resolves the managed connectors it requires,
and turns the tools those sources report into the snapshot's tool groups and
execution routes.

`_materialize` imports this lazily, because it pulls in the MCP client stack and
the offline paths — `plugins inspect` above all — must not pay for that. Both
sources arrive through a port, so nothing here reaches the Host and the
conformance suite can drive the whole pipeline without a network.

Candidate building mirrors the TypeScript runtime's `plugins/materializer.ts`
step for step, because the two runtimes share fixtures and a divergence here is
a divergence in checked-in expected output.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import json
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import JsonValue, TypeAdapter

from vibe.core.plugins._canonical import canonical_json, canonical_json_digest
from vibe.core.plugins._compatibility import PluginToolOverride
from vibe.core.plugins._naming import (
    ToolGroupIdentity,
    plugin_mcp_group_name,
    resolve_tool_group_names,
    tool_function_name,
)
from vibe.core.plugins._native import (
    PluginConfigIssue,
    PluginConnectorDefinition,
    PluginDescriptor,
    PluginMCPServerDefinition,
    ResolvedPluginSet,
)
from vibe.core.plugins._redaction import mcp_server_secrets, redact_failure
from vibe.core.plugins._snapshot import PluginSourceKind, PluginToolExposure
from vibe.core.tools.remote import RemoteTool

if TYPE_CHECKING:
    from vibe.core.plugins._materialize import (
        PluginToolCatalog,
        PluginToolGroup,
        PluginToolRoute,
    )
    from vibe.core.tools.connectors.connector_registry import (
        ConnectorRegistry,
        ConnectorToolDefinition,
    )
    from vibe.core.tools.mcp.registry import MCPRegistry

type _Source = tuple[str, str]

_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class PluginMCPDiscovery(Protocol):
    """Connects one plugin-owned MCP server and reports the tools it declares."""

    async def discover(
        self, definition: PluginMCPServerDefinition
    ) -> tuple[RemoteTool, ...]: ...


class PluginConnectorCatalog(Protocol):
    def tools(self, source_id: str) -> tuple[ConnectorToolDefinition, ...] | None: ...


class PluginMCPDiscoveryError(Exception):
    """A plugin-owned MCP server did not answer discovery."""


class PluginMCPAuthorizationRequired(Exception):
    """A plugin-owned MCP server will not list its tools until it is authorized."""


class RegistryMCPDiscovery:
    def __init__(self, registry: MCPRegistry) -> None:
        self._registry = registry

    async def discover(
        self, definition: PluginMCPServerDefinition
    ) -> tuple[RemoteTool, ...]:
        registry = self._registry.clone_configuration()
        await registry.get_tools_async([definition.server])
        failure = registry.pop_failed().get(definition.private_alias)
        if failure is not None:
            raise PluginMCPDiscoveryError(failure)
        # An unauthorized server is neither a failure nor an empty catalogue:
        # the registry parks it and reports nothing, so without this it would
        # count as connected and take every override naming it down as unused.
        if definition.private_alias in registry.needs_auth:
            raise PluginMCPAuthorizationRequired(definition.source_id)
        return registry.descriptors_for(definition.private_alias)


class RegistryConnectorCatalog:
    def __init__(self, registry: ConnectorRegistry) -> None:
        self._registry = registry

    def tools(self, source_id: str) -> tuple[ConnectorToolDefinition, ...] | None:
        if source_id not in self._registry.get_connector_names():
            return None
        return self._registry.get_catalog_tools(source_id)


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One tool that survived its source, before collisions are settled."""

    kind: PluginSourceKind
    plugin_name: str
    group_name: str
    function_name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    exposure: PluginToolExposure
    source_id: str
    source_tool_name: str
    execution_name: str
    schema_fingerprint: str
    config_file: Path

    @property
    def source(self) -> _Source:
        return (self.plugin_name, self.source_id)

    @property
    def qualified_name(self) -> str:
        return f"{self.group_name}.{self.function_name}"


async def build_tool_catalog(
    resolution: ResolvedPluginSet,
    *,
    mcp_discovery: PluginMCPDiscovery | None,
    connector_catalog: PluginConnectorCatalog | None,
    issues: list[PluginConfigIssue],
) -> PluginToolCatalog:
    """Build the tool groups and execution routes a plugin set resolves to."""
    from vibe.core.plugins._materialize import (
        PluginToolCatalog,
        PluginToolDefinition,
        PluginToolGroup,
        PluginToolRoute,
    )

    plugins = {plugin.name: plugin for plugin in resolution.plugins}
    connected = await _connect_servers(
        resolution.mcp_servers, plugins, mcp_discovery, issues
    )
    mcp_sources = frozenset(
        (definition.plugin_name, definition.source_id) for definition, _ in connected
    )
    candidates = _mcp_candidates(connected, plugins, issues)
    connector_candidates, connector_sources = _connector_candidates(
        resolution.connectors, plugins, connector_catalog, issues
    )
    candidates.extend(connector_candidates)
    candidates = _apply_group_names(candidates, resolution)
    selected = _drop_name_collisions(candidates, plugins, issues)
    _report_unused_overrides(
        resolution.plugins, mcp_sources | connector_sources, selected, issues
    )

    by_group: dict[tuple[str, str], list[_Candidate]] = {}
    for candidate in selected:
        by_group.setdefault((candidate.plugin_name, candidate.group_name), []).append(
            candidate
        )

    groups: list[PluginToolGroup] = []
    routes: dict[tuple[str, str], PluginToolRoute] = {}
    for (plugin_name, group_name), members in sorted(by_group.items()):
        ordered = sorted(members, key=lambda item: item.function_name)
        groups.append(
            PluginToolGroup(
                plugin_name=plugin_name,
                name=group_name,
                description=plugins[plugin_name].description,
                tools=tuple(
                    PluginToolDefinition(
                        name=candidate.function_name,
                        description=candidate.description,
                        input_schema=candidate.input_schema,
                        output_schema=candidate.output_schema,
                        exposure=candidate.exposure,
                    )
                    for candidate in ordered
                ),
            )
        )
        for candidate in ordered:
            routes[(group_name, candidate.function_name)] = PluginToolRoute(
                plugin_name=plugin_name,
                group_name=group_name,
                function_name=candidate.function_name,
                source_kind=candidate.kind,
                source_id=candidate.source_id,
                source_tool_name=candidate.source_tool_name,
                execution_name=candidate.execution_name,
                schema_fingerprint=candidate.schema_fingerprint,
            )
    return PluginToolCatalog(
        tool_groups=tuple(groups),
        tool_routes=MappingProxyType(routes),
        connected_mcp_sources=mcp_sources,
        connected_connector_sources=frozenset(connector_sources),
    )


async def _connect_servers(
    definitions: Sequence[PluginMCPServerDefinition],
    plugins: Mapping[str, PluginDescriptor],
    discovery: PluginMCPDiscovery | None,
    issues: list[PluginConfigIssue],
) -> list[tuple[PluginMCPServerDefinition, tuple[RemoteTool, ...]]]:
    owned = [
        definition for definition in definitions if definition.plugin_name in plugins
    ]
    if not owned or discovery is None:
        return []

    results = await asyncio.gather(
        *(discovery.discover(definition) for definition in owned),
        return_exceptions=True,
    )
    connected: list[tuple[PluginMCPServerDefinition, tuple[RemoteTool, ...]]] = []
    for definition, result in zip(owned, results, strict=True):
        if isinstance(result, PluginMCPAuthorizationRequired):
            issues.append(
                _issue(
                    plugins[definition.plugin_name],
                    definition.config_file,
                    "plugin.mcp.authorization_required",
                    f"MCP server {definition.source_id!r} offers no tools until it "
                    "is authorized.",
                    "mcp_server",
                )
            )
            continue
        if isinstance(result, BaseException):
            # The client authored this text, not Vibe, and an HTTP status error
            # quotes the request URL with its query string.
            detail = redact_failure(str(result), mcp_server_secrets(definition.server))
            issues.append(
                _issue(
                    plugins[definition.plugin_name],
                    definition.config_file,
                    "plugin.mcp.connection_failed",
                    f"MCP server {definition.source_id!r} could not be connected: "
                    f"{detail}",
                    "mcp_server",
                )
            )
            continue
        connected.append((definition, result))
    return connected


def _mcp_candidates(
    connected: Sequence[tuple[PluginMCPServerDefinition, tuple[RemoteTool, ...]]],
    plugins: Mapping[str, PluginDescriptor],
    issues: list[PluginConfigIssue],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for definition, tools in connected:
        plugin = plugins[definition.plugin_name]
        for tool in sorted(tools, key=lambda item: item.name):
            schemas = _digestible_schemas(tool.input_schema, tool.output_schema)
            if schemas is None:
                issues.append(
                    _issue(
                        plugin,
                        definition.config_file,
                        "plugin.mcp.tool_schema_invalid",
                        f"MCP tool {json.dumps(tool.name)} returned an invalid "
                        "JSON schema.",
                        "tool",
                    )
                )
                continue
            input_schema, output_schema = schemas
            override = plugin.tool_overrides.get(f"{definition.source_id}/{tool.name}")
            candidates.append(
                _Candidate(
                    kind="mcp",
                    plugin_name=plugin.name,
                    group_name=plugin_mcp_group_name(
                        definition.plugin_namespace, definition.source_id
                    ),
                    function_name=tool_function_name(
                        tool.name, override.name if override is not None else None
                    ),
                    description=tool.description
                    or f"MCP tool {json.dumps(tool.name)} from {definition.source_id}.",
                    input_schema=input_schema,
                    output_schema=output_schema,
                    exposure=_exposure(override),
                    source_id=definition.source_id,
                    source_tool_name=tool.name,
                    execution_name=f"{plugin.name}/{definition.source_id}/{tool.name}",
                    schema_fingerprint=_schema_fingerprint(
                        tool.name, input_schema, output_schema
                    ),
                    config_file=definition.config_file,
                )
            )
    return candidates


def _connector_candidates(
    definitions: Iterable[PluginConnectorDefinition],
    plugins: Mapping[str, PluginDescriptor],
    catalog: PluginConnectorCatalog | None,
    issues: list[PluginConfigIssue],
) -> tuple[list[_Candidate], set[_Source]]:
    """Candidates plus the sources that answered, for unused-override reporting."""
    candidates: list[_Candidate] = []
    answered: set[_Source] = set()
    for definition in definitions:
        plugin = plugins.get(definition.plugin_name)
        if plugin is None:
            continue
        if catalog is None:
            issues.append(
                _issue(
                    plugin,
                    definition.config_file,
                    "plugin.connector.runtime_unavailable",
                    f"Managed connector {json.dumps(definition.source_id)} is "
                    "unavailable because this Runtime has no connector registry.",
                    "connector",
                )
            )
            continue
        available = catalog.tools(definition.source_id)
        if available is None:
            issues.append(
                _issue(
                    plugin,
                    definition.config_file,
                    "plugin.connector.unavailable",
                    f"Managed connector {json.dumps(definition.source_id)} is not "
                    "available to this account.",
                    "connector",
                )
            )
            continue
        answered.add((definition.plugin_name, definition.source_id))
        by_name = {tool.name: tool for tool in available}
        for source_tool_name in definition.tools:
            tool = by_name.get(source_tool_name)
            if tool is None:
                issues.append(
                    _issue(
                        plugin,
                        definition.config_file,
                        "plugin.connector.tool_unavailable",
                        f"Managed connector {json.dumps(definition.source_id)} has "
                        f"no available tool {json.dumps(source_tool_name)}.",
                        "connector",
                    )
                )
                continue
            schemas = _digestible_schemas(tool.input_schema, None)
            if schemas is None:
                issues.append(
                    _issue(
                        plugin,
                        definition.config_file,
                        "plugin.connector.tool_schema_invalid",
                        f"Managed connector tool {json.dumps(source_tool_name)} "
                        "returned an invalid JSON schema.",
                        "connector",
                    )
                )
                continue
            input_schema, output_schema = schemas
            override = plugin.tool_overrides.get(
                f"{definition.source_id}/{source_tool_name}"
            )
            candidates.append(
                _Candidate(
                    kind="connector",
                    plugin_name=plugin.name,
                    group_name=plugin.namespace,
                    function_name=tool_function_name(
                        source_tool_name,
                        override.name if override is not None else None,
                    ),
                    description=tool.description
                    or f"Connector tool {json.dumps(source_tool_name)} from "
                    f"{definition.source_id}.",
                    input_schema=input_schema,
                    output_schema=output_schema,
                    exposure=_exposure(override),
                    source_id=definition.source_id,
                    source_tool_name=source_tool_name,
                    execution_name=(
                        f"{plugin.name}/connector/{definition.source_id}/"
                        f"{source_tool_name}"
                    ),
                    schema_fingerprint=_schema_fingerprint(
                        source_tool_name, input_schema, output_schema
                    ),
                    config_file=definition.config_file,
                )
            )
    return candidates, answered


def _apply_group_names(
    candidates: Sequence[_Candidate], resolution: ResolvedPluginSet
) -> list[_Candidate]:
    identities = {
        candidate.source: ToolGroupIdentity(
            plugin_name=candidate.plugin_name,
            base_name=candidate.group_name,
            source_id=candidate.source_id,
        )
        for candidate in candidates
        if candidate.kind == "mcp"
    }
    resolved = resolve_tool_group_names(
        identities.values(), claimed={plugin.namespace for plugin in resolution.plugins}
    )
    return [
        candidate
        if candidate.kind != "mcp"
        else replace(candidate, group_name=resolved[identities[candidate.source]])
        for candidate in candidates
    ]


def _drop_name_collisions(
    candidates: Sequence[_Candidate],
    plugins: Mapping[str, PluginDescriptor],
    issues: list[PluginConfigIssue],
) -> list[_Candidate]:
    by_name: dict[tuple[str, str], list[_Candidate]] = {}
    for candidate in candidates:
        by_name.setdefault((candidate.group_name, candidate.function_name), []).append(
            candidate
        )

    selected: list[_Candidate] = []
    for (group_name, function_name), matches in sorted(by_name.items()):
        if len(matches) == 1:
            selected.append(matches[0])
            continue
        plugin = plugins[matches[0].plugin_name]
        sources = ", ".join(
            sorted(f"{match.source_id}/{match.source_tool_name}" for match in matches)
        )
        issues.append(
            _issue(
                plugin,
                matches[0].config_file,
                "plugin.tool.name_collision",
                f"Plugin {json.dumps(plugin.name)} tools {sources} collide as "
                f"{json.dumps(function_name)} in group {json.dumps(group_name)}; "
                "add explicit toolOverrides.",
                "tool",
            )
        )
    return sorted(selected, key=lambda candidate: candidate.qualified_name)


def _report_unused_overrides(
    descriptors: Iterable[PluginDescriptor],
    answered: frozenset[_Source] | set[_Source],
    selected: Sequence[_Candidate],
    issues: list[PluginConfigIssue],
) -> None:
    discovered = {
        (candidate.plugin_name, candidate.source_id, candidate.source_tool_name)
        for candidate in selected
    }
    for plugin in descriptors:
        for key in sorted(plugin.tool_overrides):
            source_id, _, source_tool_name = key.partition("/")
            if not source_id or (plugin.name, source_id) not in answered:
                continue
            if (plugin.name, source_id, source_tool_name) in discovered:
                continue
            issues.append(
                _issue(
                    plugin,
                    plugin.manifest_path,
                    "plugin.tool_override.unused",
                    f"Tool override {json.dumps(key)} does not match a tool.",
                    "tool",
                )
            )


def _exposure(override: PluginToolOverride | None) -> PluginToolExposure:
    if override is not None and override.exposure is not None:
        return override.exposure
    return "programmatic"


def _digestible_schemas(
    input_schema: Any, output_schema: Any
) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    try:
        coerced_input = _JSON_VALUE.validate_python(dict(input_schema))
        coerced_output = (
            None
            if output_schema is None
            else _JSON_VALUE.validate_python(dict(output_schema))
        )
        canonical_json(coerced_input)
        if coerced_output is not None:
            canonical_json(coerced_output)
    except (TypeError, ValueError):
        return None
    if not isinstance(coerced_input, dict):
        return None
    if coerced_output is not None and not isinstance(coerced_output, dict):
        return None
    return coerced_input, coerced_output


def _schema_fingerprint(
    name: str, input_schema: Mapping[str, Any], output_schema: Mapping[str, Any] | None
) -> str:
    return canonical_json_digest({
        "name": name,
        "inputSchema": dict(input_schema),
        "outputSchema": None if output_schema is None else dict(output_schema),
    })


def _issue(
    plugin: PluginDescriptor, file: Path, code: str, message: str, component: str
) -> PluginConfigIssue:
    return PluginConfigIssue(
        file=file,
        message=message,
        severity="warning",
        code=code,
        fatal=False,
        source_format=plugin.source_format,
        component=component,
    )
