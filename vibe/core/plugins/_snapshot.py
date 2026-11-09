from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vibe.core.plugins._canonical import NormalizedJson, NormalizedStr, canonical_json
from vibe.core.plugins._paths import PluginPathRef

PluginSourceFormat = Literal[
    "agent_plugins_1_0", "claude_code", "codex", "kimi_code", "opencode"
]
PluginToolExposure = Literal["programmatic", "direct", "direct_and_programmatic"]
PluginSourceKind = Literal["mcp", "connector"]

HOST_HOOK_ENVIRONMENT = frozenset({"PLUGIN_ROOT", "PLUGIN_DATA"})


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _OwnedModel(_SnapshotModel):
    plugin_name: NormalizedStr


class PluginSnapshotEntry(_SnapshotModel):
    name: NormalizedStr
    namespace: NormalizedStr
    version: NormalizedStr | None = None
    source_format: PluginSourceFormat
    manifest_digest: str


class PluginSkillSnapshot(_OwnedModel):
    name: NormalizedStr
    description: NormalizedStr
    path: PluginPathRef


class PluginKnowledgeSnapshot(_OwnedModel):
    name: NormalizedStr
    source_name: NormalizedStr
    description: NormalizedStr
    display_name: NormalizedStr | None = None
    icon: NormalizedStr | None = None
    path: PluginPathRef
    entrypoint: PluginPathRef


class PluginAgentSnapshot(_OwnedModel):
    name: NormalizedStr
    source_name: NormalizedStr
    display_name: NormalizedStr
    description: NormalizedStr
    path: PluginPathRef
    safety: NormalizedStr
    instructions: NormalizedStr | None = None
    overrides: NormalizedJson = None


class PluginHookSnapshot(_OwnedModel):
    declared_name: NormalizedStr
    source: NormalizedStr
    protocol: NormalizedStr
    visibility: NormalizedStr
    order: int
    config: NormalizedJson = None
    config_file: PluginPathRef | None = None
    cwd: PluginPathRef | None = None
    environment_names: tuple[NormalizedStr, ...] = ()


class PluginLibrarySnapshot(_OwnedModel):
    language: NormalizedStr
    alias: NormalizedStr
    source_path: PluginPathRef


# MCP servers are deliberately absent: the snapshot carries the tool catalogue
# they produced, never their definitions. Connectors are registry-backed, so
# they carry no URL, no environment, and no headers.
class PluginConnectorSnapshot(_OwnedModel):
    source_id: NormalizedStr
    tools: tuple[NormalizedStr, ...] = ()


class PluginToolSnapshot(_SnapshotModel):
    name: NormalizedStr
    description: NormalizedStr
    input_schema: NormalizedJson = None
    output_schema: NormalizedJson = None
    exposure: PluginToolExposure


class PluginToolGroupSnapshot(_OwnedModel):
    name: NormalizedStr
    description: NormalizedStr
    tools: tuple[PluginToolSnapshot, ...] = ()


class PluginToolRouteSnapshot(_OwnedModel):
    group_name: NormalizedStr
    function_name: NormalizedStr
    source_kind: PluginSourceKind
    source_id: NormalizedStr
    source_tool_name: NormalizedStr
    execution_name: NormalizedStr
    schema_fingerprint: str


class ResolvedPluginSnapshot(_SnapshotModel):
    version: Literal[1] = 1
    plugins: tuple[PluginSnapshotEntry, ...] = ()
    skills: tuple[PluginSkillSnapshot, ...] = ()
    knowledge: tuple[PluginKnowledgeSnapshot, ...] = ()
    agents: tuple[PluginAgentSnapshot, ...] = ()
    hooks: tuple[PluginHookSnapshot, ...] = ()
    libraries: tuple[PluginLibrarySnapshot, ...] = ()
    connectors: tuple[PluginConnectorSnapshot, ...] = ()
    tool_groups: tuple[PluginToolGroupSnapshot, ...] = ()
    tool_routes: tuple[PluginToolRouteSnapshot, ...] = ()


class PluginSnapshotIdentityError(ValueError):
    def __init__(self, names: Iterable[str]) -> None:
        super().__init__(f"snapshot references unknown plugins: {sorted(names)}")
        self.names = tuple(sorted(names))


def hook_environment_names(environment: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(_portable_environment(environment)))


def build_plugin_snapshot(
    entries: Iterable[PluginSnapshotEntry],
    *,
    skills: Iterable[PluginSkillSnapshot] = (),
    knowledge: Iterable[PluginKnowledgeSnapshot] = (),
    agents: Iterable[PluginAgentSnapshot] = (),
    hooks: Iterable[PluginHookSnapshot] = (),
    libraries: Iterable[PluginLibrarySnapshot] = (),
    connectors: Iterable[PluginConnectorSnapshot] = (),
    tool_groups: Iterable[PluginToolGroupSnapshot] = (),
    tool_routes: Iterable[PluginToolRouteSnapshot] = (),
) -> ResolvedPluginSnapshot:
    ordered_entries = tuple(sorted(entries, key=lambda item: item.name))
    catalog = _CatalogView(
        skills=tuple(sorted(skills, key=lambda item: item.name)),
        knowledge=tuple(sorted(knowledge, key=lambda item: item.name)),
        agents=tuple(sorted(agents, key=lambda item: item.name)),
        hooks=tuple(hooks),
        libraries=tuple(
            sorted(libraries, key=lambda item: (item.language, item.alias))
        ),
        connectors=tuple(
            sorted(connectors, key=lambda item: (item.plugin_name, item.source_id))
        ),
        tool_groups=tuple(
            group.model_copy(
                update={"tools": tuple(sorted(group.tools, key=lambda item: item.name))}
            )
            for group in sorted(tool_groups, key=lambda item: item.name)
        ),
        tool_routes=tuple(sorted(tool_routes, key=_route_sort_key)),
    )
    catalog.reject_unknown_owners({entry.name for entry in ordered_entries})
    return ResolvedPluginSnapshot(
        plugins=ordered_entries,
        skills=catalog.skills,
        knowledge=catalog.knowledge,
        agents=catalog.agents,
        hooks=catalog.hooks,
        libraries=catalog.libraries,
        connectors=catalog.connectors,
        tool_groups=catalog.tool_groups,
        tool_routes=catalog.tool_routes,
    )


def snapshot_bytes(snapshot: ResolvedPluginSnapshot) -> bytes:
    """The bytes a pinned session stores, and the exact input to its digest."""
    return canonical_json(snapshot.model_dump(mode="json"))


def snapshot_digest(snapshot: ResolvedPluginSnapshot) -> str:
    return hashlib.sha256(snapshot_bytes(snapshot)).hexdigest()


def validate_resolved_plugin_snapshot(snapshot: ResolvedPluginSnapshot) -> None:
    catalog = _CatalogView(
        skills=snapshot.skills,
        knowledge=snapshot.knowledge,
        agents=snapshot.agents,
        hooks=snapshot.hooks,
        libraries=snapshot.libraries,
        connectors=snapshot.connectors,
        tool_groups=snapshot.tool_groups,
        tool_routes=snapshot.tool_routes,
    )
    catalog.reject_unknown_owners({entry.name for entry in snapshot.plugins})


class _CatalogView(_SnapshotModel):
    skills: tuple[PluginSkillSnapshot, ...]
    knowledge: tuple[PluginKnowledgeSnapshot, ...]
    agents: tuple[PluginAgentSnapshot, ...]
    hooks: tuple[PluginHookSnapshot, ...]
    libraries: tuple[PluginLibrarySnapshot, ...]
    connectors: tuple[PluginConnectorSnapshot, ...]
    tool_groups: tuple[PluginToolGroupSnapshot, ...]
    tool_routes: tuple[PluginToolRouteSnapshot, ...]

    def reject_unknown_owners(self, known: set[str]) -> None:
        """Every component, and every path it points at, names a known plugin.

        A ``PluginPathRef`` is resolved against the checkout of the plugin it
        names, so a reference to a plugin the snapshot does not carry has no
        root to join against and would resolve wherever the reader happens to
        stand.
        """
        unknown = {
            name
            for collection in self.collections
            for item in collection
            for name in _referenced_plugins(item)
            if name not in known
        }
        if unknown:
            raise PluginSnapshotIdentityError(unknown)

    @property
    def collections(self) -> tuple[tuple[_OwnedModel, ...], ...]:
        return (
            self.skills,
            self.knowledge,
            self.agents,
            self.hooks,
            self.libraries,
            self.connectors,
            self.tool_groups,
            self.tool_routes,
        )


def _referenced_plugins(item: _OwnedModel) -> Iterator[str]:
    yield item.plugin_name
    for value in vars(item).values():
        if isinstance(value, PluginPathRef):
            yield value.plugin


def _route_sort_key(route: PluginToolRouteSnapshot) -> tuple[str, ...]:
    return (
        route.plugin_name,
        route.group_name,
        route.function_name,
        route.source_id,
        route.source_tool_name,
    )


def _portable_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in environment.items()
        if name not in HOST_HOOK_ENVIRONMENT
    }
