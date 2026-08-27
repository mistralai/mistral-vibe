from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from vibe.core.plugins._native import (
    PluginConfigIssue,
    PluginKnowledgeDefinition,
    PluginLibraryDefinition,
    ResolvedPluginSet,
)
from vibe.core.plugins._snapshot import PluginSourceKind, PluginToolExposure

if TYPE_CHECKING:
    from vibe.core.plugins._catalog import PluginConnectorCatalog, PluginMCPDiscovery

_PLUGIN_NODE_PATHS_ENV = "UNIFIED_HARNESS_PLUGIN_NODE_PATHS"
_NODE_LOADER_REGISTER = """import { register } from \"node:module\";

register(new URL(\"./loader-hooks.mjs\", import.meta.url));
"""
_NODE_LOADER_HOOKS = """import { createRequire } from \"node:module\";
import path from \"node:path\";
import { pathToFileURL } from \"node:url\";

const roots = (process.env.UNIFIED_HARNESS_PLUGIN_NODE_PATHS ?? \"\")
  .split(path.delimiter)
  .filter(Boolean);
const resolvers = roots.map((root) =>
  createRequire(pathToFileURL(path.join(root, \".unified-harness-resolver.cjs\")))
);

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (originalError) {
    if (
      specifier.startsWith(\".\") ||
      specifier.startsWith(\"/\") ||
      specifier.includes(\":\")
    ) {
      throw originalError;
    }
    for (const resolver of resolvers) {
      try {
        const resolved = resolver.resolve(specifier);
        return await nextResolve(pathToFileURL(resolved).href, context);
      } catch {
        // Try the next Runtime-owned package root.
      }
    }
    throw originalError;
  }
}
"""


@dataclass(frozen=True, slots=True)
class PluginToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    exposure: PluginToolExposure


@dataclass(frozen=True, slots=True)
class PluginToolGroup:
    plugin_name: str
    name: str
    description: str
    tools: tuple[PluginToolDefinition, ...]


@dataclass(frozen=True, slots=True)
class PluginToolRoute:
    plugin_name: str
    group_name: str
    function_name: str
    source_kind: PluginSourceKind
    source_id: str
    source_tool_name: str
    execution_name: str
    schema_fingerprint: str


@dataclass(frozen=True, slots=True)
class PluginToolCatalog:
    tool_groups: tuple[PluginToolGroup, ...]
    tool_routes: Mapping[tuple[str, str], PluginToolRoute]
    # Connected means the source answered, including with an empty catalog.
    connected_mcp_sources: frozenset[tuple[str, str]]
    connected_connector_sources: frozenset[tuple[str, str]]

    @classmethod
    def empty(cls) -> PluginToolCatalog:
        return cls(
            tool_groups=(),
            tool_routes=MappingProxyType({}),
            connected_mcp_sources=frozenset(),
            connected_connector_sources=frozenset(),
        )


@dataclass(frozen=True, slots=True)
class MaterializedPluginSet:
    resolution: ResolvedPluginSet
    # A tool group is the catalog a plugin's declared sources answered with, so
    # it exists only once those sources are connected. A materializer built
    # without the ports that connect them leaves both empty.
    tool_groups: tuple[PluginToolGroup, ...]
    tool_routes: Mapping[tuple[str, str], PluginToolRoute]
    connected_mcp_sources: frozenset[tuple[str, str]]
    connected_connector_sources: frozenset[tuple[str, str]]
    knowledge: tuple[PluginKnowledgeDefinition, ...]
    libraries: tuple[PluginLibraryDefinition, ...]
    process_environment: Mapping[str, str]
    issues: tuple[PluginConfigIssue, ...]

    @classmethod
    def empty(cls, resolution: ResolvedPluginSet) -> MaterializedPluginSet:
        return cls(
            resolution=resolution,
            tool_groups=(),
            tool_routes=MappingProxyType({}),
            connected_mcp_sources=frozenset(),
            connected_connector_sources=frozenset(),
            knowledge=(),
            libraries=(),
            process_environment=MappingProxyType({}),
            issues=resolution.issues,
        )


class PluginMaterializer:
    def __init__(
        self,
        *,
        mcp_discovery: PluginMCPDiscovery | None = None,
        connector_catalog: PluginConnectorCatalog | None = None,
    ) -> None:
        self._mcp_discovery = mcp_discovery
        self._connector_catalog = connector_catalog

    async def materialize(self, resolution: ResolvedPluginSet) -> MaterializedPluginSet:
        issues = list(resolution.issues)
        async with asyncio.TaskGroup() as tasks:
            knowledge_task = tasks.create_task(
                asyncio.to_thread(self._materialize_knowledge, resolution, issues)
            )
            libraries_task = tasks.create_task(
                asyncio.to_thread(self._materialize_libraries, resolution, issues)
            )
            tools_task = tasks.create_task(self._materialize_tools(resolution, issues))

        knowledge = knowledge_task.result()
        libraries, process_environment = libraries_task.result()
        catalog = tools_task.result()
        return MaterializedPluginSet(
            resolution=resolution,
            tool_groups=catalog.tool_groups,
            tool_routes=catalog.tool_routes,
            connected_mcp_sources=catalog.connected_mcp_sources,
            connected_connector_sources=catalog.connected_connector_sources,
            knowledge=knowledge,
            libraries=libraries,
            process_environment=process_environment,
            issues=tuple(issues),
        )

    async def _materialize_tools(
        self, resolution: ResolvedPluginSet, issues: list[PluginConfigIssue]
    ) -> PluginToolCatalog:
        if not resolution.mcp_servers and not resolution.connectors:
            return PluginToolCatalog.empty()
        from vibe.core.plugins._catalog import build_tool_catalog

        return await build_tool_catalog(
            resolution,
            mcp_discovery=self._mcp_discovery,
            connector_catalog=self._connector_catalog,
            issues=issues,
        )

    @staticmethod
    def _materialize_knowledge(
        resolution: ResolvedPluginSet, issues: list[PluginConfigIssue]
    ) -> tuple[PluginKnowledgeDefinition, ...]:
        materialized: list[PluginKnowledgeDefinition] = []
        plugins = {plugin.name: plugin for plugin in resolution.plugins}
        for definition in resolution.knowledge:
            plugin = plugins[definition.plugin_name]
            try:
                PluginMaterializer._seed_knowledge(definition, plugin.data_root)
            except OSError as error:
                issues.append(
                    PluginConfigIssue(
                        file=definition.runtime_root,
                        message=f"Failed to prepare plugin knowledge: {error}",
                        code="plugin.knowledge.materialization_failed",
                        source_format=plugin.source_format,
                        component="knowledge",
                    )
                )
                continue
            materialized.append(definition)
        return tuple(materialized)

    @staticmethod
    def _seed_knowledge(definition: PluginKnowledgeDefinition, data_root: Path) -> None:
        parent = definition.runtime_root.parent
        parent.mkdir(parents=True, exist_ok=True)
        if not definition.runtime_root.exists():
            temporary = parent / f".{definition.source_name}-{uuid4().hex}"
            temporary.mkdir()
            staged = temporary / "content"
            try:
                shutil.copytree(definition.source_root, staged)
                try:
                    os.rename(staged, definition.runtime_root)
                except FileExistsError:
                    pass
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
        resolved_data_root = data_root.resolve(strict=True)
        resolved_runtime_root = definition.runtime_root.resolve(strict=True)
        resolved_entrypoint = definition.runtime_entrypoint.resolve(strict=True)
        if (
            not resolved_runtime_root.is_relative_to(resolved_data_root)
            or not resolved_runtime_root.is_dir()
            or not resolved_entrypoint.is_relative_to(resolved_runtime_root)
            or not resolved_entrypoint.is_file()
        ):
            raise OSError("materialized knowledge path escapes the plugin data root")

    @staticmethod
    def _materialize_libraries(
        resolution: ResolvedPluginSet, issues: list[PluginConfigIssue]
    ) -> tuple[tuple[PluginLibraryDefinition, ...], Mapping[str, str]]:
        materialized: list[PluginLibraryDefinition] = []
        plugins = {plugin.name: plugin for plugin in resolution.plugins}
        node_available = shutil.which("node") is not None
        for definition in resolution.libraries:
            plugin = plugins[definition.plugin_name]
            if definition.language == "node" and not node_available:
                issues.append(
                    PluginConfigIssue(
                        file=definition.config_file,
                        message=(
                            f"Node plugin library {definition.alias!r} is unavailable "
                            "because Node.js is not installed"
                        ),
                        code="plugin.library.runtime_unavailable",
                        source_format=plugin.source_format,
                        component="library",
                    )
                )
                continue
            try:
                PluginMaterializer._seed_library(definition, plugin.data_root)
            except OSError as error:
                issues.append(
                    PluginConfigIssue(
                        file=definition.source_path,
                        message=(
                            f"Failed to prepare {definition.language} plugin library "
                            f"{definition.alias!r}: {error}"
                        ),
                        code="plugin.library.materialization_failed",
                        source_format=plugin.source_format,
                        component="library",
                    )
                )
                continue
            materialized.append(definition)
        environment = PluginMaterializer._library_environment(
            tuple(materialized), resolution
        )
        return tuple(materialized), MappingProxyType(environment)

    @staticmethod
    def _seed_library(definition: PluginLibraryDefinition, data_root: Path) -> None:
        destination = definition.runtime_path
        parent = destination.parent
        parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() and not destination.is_symlink():
            temporary = parent / f".{destination.name}-{uuid4().hex}"
            try:
                if definition.source_path.is_dir():
                    shutil.copytree(definition.source_path, temporary)
                else:
                    shutil.copy2(definition.source_path, temporary)
                try:
                    os.rename(temporary, destination)
                except FileExistsError:
                    pass
            finally:
                if temporary.is_dir():
                    shutil.rmtree(temporary, ignore_errors=True)
                else:
                    temporary.unlink(missing_ok=True)

        resolved_data_root = data_root.resolve(strict=True)
        resolved_destination = destination.resolve(strict=True)
        if not resolved_destination.is_relative_to(resolved_data_root):
            raise OSError("materialized library path escapes the plugin data root")
        if definition.source_path.is_dir() != resolved_destination.is_dir():
            raise OSError("materialized library has the wrong filesystem type")
        if definition.source_path.is_file() != resolved_destination.is_file():
            raise OSError("materialized library has the wrong filesystem type")

    @staticmethod
    def _library_environment(
        libraries: Sequence[PluginLibraryDefinition], resolution: ResolvedPluginSet
    ) -> dict[str, str]:
        node_roots = sorted({
            _node_modules_root(definition.runtime_path)
            for definition in libraries
            if definition.language == "node"
        })
        python_roots = sorted({
            definition.runtime_path.parent
            for definition in libraries
            if definition.language == "python"
        })
        environment: dict[str, str] = {}
        if python_roots:
            environment["PYTHONPATH"] = _prepend_search_paths(
                python_roots, os.environ.get("PYTHONPATH")
            )
        if not node_roots:
            return environment

        environment["NODE_PATH"] = _prepend_search_paths(
            node_roots, os.environ.get("NODE_PATH")
        )
        environment[_PLUGIN_NODE_PATHS_ENV] = os.pathsep.join(map(str, node_roots))
        loader_root = (
            resolution.plugins[0].data_root.parent / ".runtime" / "node-loader-v1"
        )
        loader_root.mkdir(parents=True, exist_ok=True)
        register_file = loader_root / "register.mjs"
        _write_runtime_file(register_file, _NODE_LOADER_REGISTER)
        _write_runtime_file(loader_root / "loader-hooks.mjs", _NODE_LOADER_HOOKS)
        loader_option = f"--import={register_file.resolve(strict=True).as_uri()}"
        existing_options = os.environ.get("NODE_OPTIONS", "").strip()
        environment["NODE_OPTIONS"] = " ".join(
            option for option in (existing_options, loader_option) if option
        )
        return environment


def _node_modules_root(runtime_path: Path) -> Path:
    for parent in runtime_path.parents:
        if parent.name == "node_modules":
            return parent
    raise OSError(f"Node library path has no node_modules root: {runtime_path}")


def _prepend_search_paths(paths: Sequence[Path], existing: str | None) -> str:
    values = [*(str(path) for path in paths)]
    if existing:
        values.append(existing)
    return os.pathsep.join(values)


def _write_runtime_file(path: Path, content: str) -> None:
    try:
        if path.read_text(encoding="utf-8") == content:
            return
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}-{uuid4().hex}")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
