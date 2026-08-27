from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from vibe.core.hooks.models import RuntimeHookDefinition
from vibe.core.plugins._compatibility import DetectedPluginFormat
from vibe.core.plugins._materialize import MaterializedPluginSet
from vibe.core.plugins._native import (
    PluginAgentDefinition,
    PluginConnectorDefinition,
    PluginDescriptor,
    PluginKnowledgeDefinition,
    PluginLibraryDefinition,
)
from vibe.core.plugins._paths import PluginPathRef, plugin_path_ref
from vibe.core.plugins._snapshot import (
    PluginAgentSnapshot,
    PluginConnectorSnapshot,
    PluginHookSnapshot,
    PluginKnowledgeSnapshot,
    PluginLibrarySnapshot,
    PluginSkillSnapshot,
    PluginSnapshotEntry,
    PluginSourceFormat,
    PluginToolGroupSnapshot,
    PluginToolRouteSnapshot,
    PluginToolSnapshot,
    ResolvedPluginSnapshot,
    build_plugin_snapshot,
    hook_environment_names,
)
from vibe.core.skills.models import SkillInfo

# Only a format that can produce a surviving plugin has a snapshot spelling.
# OpenCode is one of them: its executable modules are refused, but a package
# whose skills or declarative MCP servers are portable survives on those alone.
_SOURCE_FORMATS: Mapping[DetectedPluginFormat, PluginSourceFormat] = {
    DetectedPluginFormat.AGENT_PLUGINS_1_0: "agent_plugins_1_0",
    DetectedPluginFormat.CLAUDE_CODE: "claude_code",
    DetectedPluginFormat.CODEX: "codex",
    DetectedPluginFormat.KIMI_CODE: "kimi_code",
    DetectedPluginFormat.OPENCODE: "opencode",
}


class PluginSourceFormatUnsupportedError(ValueError):
    def __init__(self, plugin: str, source_format: DetectedPluginFormat) -> None:
        super().__init__(
            f"plugin {plugin!r} has no snapshot source format for {source_format}"
        )
        self.plugin = plugin
        self.source_format = source_format


def plugin_identity(plugin: PluginDescriptor) -> PluginSnapshotEntry:
    return PluginSnapshotEntry(
        name=plugin.name,
        namespace=plugin.namespace,
        version=plugin.version,
        source_format=source_format(plugin),
        manifest_digest=plugin.manifest_digest,
    )


def source_format(plugin: PluginDescriptor) -> PluginSourceFormat:
    resolved = _SOURCE_FORMATS.get(plugin.source_format)
    if resolved is None:
        raise PluginSourceFormatUnsupportedError(plugin.name, plugin.source_format)
    return resolved


def build_snapshot(materialized: MaterializedPluginSet) -> ResolvedPluginSnapshot:
    """Project a materialized plugin set into its portable snapshot.

    Only identity and the catalogue travel. Host paths, runtime staging paths,
    MCP server definitions, and every late-bound value — hook environment
    values, MCP ``env`` values, header values, the query string of an MCP URL —
    stay out, so two resolutions of one tree produce one byte string however
    the credentials around it are configured.
    """
    resolution = materialized.resolution
    plugins = {plugin.name: plugin for plugin in resolution.plugins}
    by_namespace = {plugin.namespace: plugin for plugin in resolution.plugins}
    return build_plugin_snapshot(
        (plugin_identity(plugin) for plugin in resolution.plugins),
        skills=_skills(resolution.skills, by_namespace),
        knowledge=_knowledge(resolution.knowledge, plugins),
        agents=_agents(resolution.agents, plugins),
        hooks=_hooks(resolution.runtime_hooks, plugins),
        libraries=_libraries(resolution.libraries, plugins),
        connectors=_connectors(resolution.connectors),
        tool_groups=_tool_groups(materialized),
        tool_routes=_tool_routes(materialized),
    )


def _skills(
    skills: Mapping[str, SkillInfo], by_namespace: Mapping[str, PluginDescriptor]
) -> list[PluginSkillSnapshot]:
    snapshots: list[PluginSkillSnapshot] = []
    for alias, skill in skills.items():
        namespace, _, _ = alias.partition(":")
        plugin = by_namespace.get(namespace)
        if plugin is None or skill.skill_path is None:
            continue
        snapshots.append(
            PluginSkillSnapshot(
                plugin_name=plugin.name,
                name=alias,
                description=skill.description,
                path=plugin_path_ref(plugin.name, plugin.root, skill.skill_path),
            )
        )
    return snapshots


def _knowledge(
    definitions: Iterable[PluginKnowledgeDefinition],
    plugins: Mapping[str, PluginDescriptor],
) -> list[PluginKnowledgeSnapshot]:
    return [
        PluginKnowledgeSnapshot(
            plugin_name=definition.plugin_name,
            name=definition.name,
            source_name=definition.source_name,
            description=definition.description,
            display_name=definition.display_name,
            icon=definition.icon,
            path=plugin_path_ref(
                definition.plugin_name,
                plugins[definition.plugin_name].root,
                definition.source_root,
            ),
            entrypoint=plugin_path_ref(
                definition.plugin_name,
                plugins[definition.plugin_name].root,
                definition.source_entrypoint,
            ),
        )
        for definition in definitions
        if definition.plugin_name in plugins
    ]


def _agents(
    definitions: Iterable[PluginAgentDefinition],
    plugins: Mapping[str, PluginDescriptor],
) -> list[PluginAgentSnapshot]:
    return [
        PluginAgentSnapshot(
            plugin_name=definition.plugin_name,
            name=definition.name,
            source_name=definition.source_name,
            display_name=definition.profile.display_name,
            description=definition.profile.description,
            path=plugin_path_ref(
                definition.plugin_name,
                plugins[definition.plugin_name].root,
                definition.source_file,
            ),
            safety=definition.profile.safety,
            instructions=definition.profile.instructions,
            overrides=_json(definition.profile.overrides),
        )
        for definition in definitions
        if definition.plugin_name in plugins
    ]


def _hooks(
    definitions: Iterable[RuntimeHookDefinition],
    plugins: Mapping[str, PluginDescriptor],
) -> list[PluginHookSnapshot]:
    snapshots: list[PluginHookSnapshot] = []
    for definition in definitions:
        plugin = plugins.get(definition.plugin_name or "")
        if plugin is None or definition.declared_name is None:
            continue
        snapshots.append(
            PluginHookSnapshot(
                plugin_name=plugin.name,
                declared_name=definition.declared_name,
                source=definition.source,
                protocol=definition.protocol,
                visibility=definition.visibility,
                order=definition.order,
                config=_json(definition.config.model_dump(mode="json")),
                config_file=_optional_ref(plugin, definition.config_file),
                cwd=_optional_ref(plugin, definition.cwd),
                environment_names=hook_environment_names(definition.environment),
            )
        )
    return snapshots


def _libraries(
    definitions: Iterable[PluginLibraryDefinition],
    plugins: Mapping[str, PluginDescriptor],
) -> list[PluginLibrarySnapshot]:
    # runtime_path is not pinned: it is derived at materialization time from the
    # data root and the content digest, neither of which belongs in a snapshot.
    return [
        PluginLibrarySnapshot(
            plugin_name=definition.plugin_name,
            language=definition.language,
            alias=definition.alias,
            source_path=plugin_path_ref(
                definition.plugin_name,
                plugins[definition.plugin_name].root,
                definition.source_path,
            ),
        )
        for definition in definitions
        if definition.plugin_name in plugins
    ]


def _connectors(
    definitions: Iterable[PluginConnectorDefinition],
) -> list[PluginConnectorSnapshot]:
    return [
        PluginConnectorSnapshot(
            plugin_name=definition.plugin_name,
            source_id=definition.source_id,
            tools=tuple(definition.tools),
        )
        for definition in definitions
    ]


def _tool_groups(materialized: MaterializedPluginSet) -> list[PluginToolGroupSnapshot]:
    snapshots: list[PluginToolGroupSnapshot] = []
    for group in materialized.tool_groups:
        snapshots.append(
            PluginToolGroupSnapshot(
                plugin_name=group.plugin_name,
                name=group.name,
                description=group.description,
                tools=tuple(
                    PluginToolSnapshot(
                        name=tool.name,
                        description=tool.description,
                        input_schema=_json(dict(tool.input_schema)),
                        output_schema=(
                            None
                            if tool.output_schema is None
                            else _json(dict(tool.output_schema))
                        ),
                        exposure=tool.exposure,
                    )
                    for tool in group.tools
                ),
            )
        )
    return snapshots


def _tool_routes(materialized: MaterializedPluginSet) -> list[PluginToolRouteSnapshot]:
    return [
        PluginToolRouteSnapshot(
            plugin_name=route.plugin_name,
            group_name=route.group_name,
            function_name=route.function_name,
            source_kind=route.source_kind,
            source_id=route.source_id,
            source_tool_name=route.source_tool_name,
            execution_name=route.execution_name,
            schema_fingerprint=route.schema_fingerprint,
        )
        for route in materialized.tool_routes.values()
    ]


def _optional_ref(plugin: PluginDescriptor, path: Path | None) -> PluginPathRef | None:
    if path is None:
        return None
    return plugin_path_ref(plugin.name, plugin.root, path)


def _json(value: Any) -> JsonValue:
    return cast("JsonValue", value)
