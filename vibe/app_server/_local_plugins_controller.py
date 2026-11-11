"""The ``plugins/local/*`` API: installed plugins listed and toggled from Desktop.

A plugin is addressed by its manifest name, which is also what
``disabled_plugins`` lists.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from vibe.app_server._dispatch import RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._projection import writable_list_field
from vibe.app_server.models import PluginComponentKind, PluginScope
from vibe.app_server.protocol import (
    LocalPluginComponent,
    LocalPluginIssue,
    LocalPluginSummary,
    LocalSkillRefusalData,
    PluginsLocalListParams,
    PluginsLocalListResponse,
    PluginsLocalSetEnabledParams,
    PluginsLocalSetEnabledResponse,
    ProtocolErrorCode,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.models import MCPServer, MCPStdio
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import VIBE_HOME
from vibe.core.plugins import (
    InstalledPlugin,
    InstalledPluginStatus,
    PluginResolver,
    PluginToggles,
    ResolvedPluginSet,
    is_required_plugin,
    set_plugin_enabled,
)
from vibe.core.skills.models import SkillScope

__all__ = ["LOCAL_PLUGIN_METHODS", "LocalPluginsController"]

LOCAL_PLUGIN_METHODS = frozenset({"plugins/local/list", "plugins/local/setEnabled"})

type SessionFiles = Callable[[Path | None], HarnessFilesManager]
type OrchestratorLoader = Callable[
    [HarnessFilesManager], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
]


class LocalPluginsController:
    def __init__(
        self,
        session_files: SessionFiles,
        load_orchestrator: OrchestratorLoader,
        builtin_plugin_roots: Callable[[], Sequence[Path]],
    ) -> None:
        """``session_files(None)`` is the user's own config, the one toggles write."""
        self._session_files = session_files
        self._load_orchestrator = load_orchestrator
        self._builtin_plugin_roots = builtin_plugin_roots

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> ProtocolModel:
        try:
            match method:
                case "plugins/local/list":
                    return await self._list(
                        validate_wire(PluginsLocalListParams, raw_params)
                    )
                case "plugins/local/setEnabled":
                    return await self._set_enabled(
                        validate_wire(PluginsLocalSetEnabledParams, raw_params)
                    )
                case _:
                    raise method_not_found(method)
        except ValidationError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

    async def _list(self, params: PluginsLocalListParams) -> PluginsLocalListResponse:
        project_root = _project_root(params.project_root)
        orchestrator, resolution = await self._read(
            project_root, params.trust_workspace
        )
        toggles = _toggles(orchestrator)
        return PluginsLocalListResponse(
            plugins=[
                _summary(plugin, resolution, toggles)
                for plugin in _listed(resolution, project_root)
            ],
            issues=[
                LocalPluginIssue(file=str(issue.file), message=issue.message)
                for issue in resolution.issues
            ],
        )

    async def _set_enabled(
        self, params: PluginsLocalSetEnabledParams
    ) -> PluginsLocalSetEnabledResponse:
        project_root = _project_root(params.project_root)
        orchestrator, resolution = await self._read(
            project_root, params.trust_workspace
        )
        listed = _listed(resolution, project_root)
        plugin = next((p for p in listed if p.descriptor.name == params.name), None)
        if plugin is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"No plugin named {params.name!r}"
            )
        toggles = _toggles(orchestrator)
        _refuse_toggle(plugin, params.enabled, toggles, resolution)
        mutation = await set_plugin_enabled(
            await self._load_orchestrator(self._session_files(None)),
            plugin.descriptor,
            params.enabled,
        )
        if mutation.failures:
            raise RequestFailure(
                ProtocolErrorCode.INTERNAL_ERROR,
                f"Failed to update configuration: {mutation.failures[0]}",
            ) from mutation.failures[0]
        # What the write did, without resolving again: an unlocked plugin is now
        # exactly what was asked, and the refusal above ruled out a collision.
        status: InstalledPluginStatus = "loaded" if params.enabled else "disabled"
        return PluginsLocalSetEnabledResponse(
            plugin=_summary(replace(plugin, status=status), resolution, toggles)
        )

    async def _read(
        self, project_root: Path | None, trust_workspace: bool
    ) -> tuple[ConfigOrchestrator[VibeConfigSchema], ResolvedPluginSet]:
        """For a project, the config and plugins a session there runs with."""
        files = self._session_files(project_root)
        if project_root is not None and trust_workspace:
            files = files.trusting(project_root)
        orchestrator = await self._load_orchestrator(files)
        resolver = PluginResolver.from_harness_files(
            files,
            builtin_roots=self._builtin_plugin_roots(),
            data_root_base=VIBE_HOME.path / "plugin-data",
            config_orchestrator=orchestrator,
        )
        return orchestrator, await asyncio.to_thread(resolver.resolve)


def _refuse_toggle(
    plugin: InstalledPlugin,
    enabled: bool,
    toggles: PluginToggles,
    resolution: ResolvedPluginSet,
) -> None:
    name = plugin.descriptor.name
    if toggles.locked(plugin.descriptor):
        reason = (
            "is part of Vibe and cannot be turned off"
            if is_required_plugin(plugin.descriptor)
            else "is fixed by configuration and cannot be toggled here"
        )
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"{name!r} {reason}",
            LocalSkillRefusalData(reason="locked").model_dump(mode="json"),
        )
    rival = _namespace_rival(plugin, resolution)
    if enabled and rival is not None:
        namespace = plugin.descriptor.namespace
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"{name!r} shares the namespace {namespace!r} with {rival!r}, which is "
            f"on: turn {rival!r} off first",
            LocalSkillRefusalData(reason="namespace_collision").model_dump(mode="json"),
        )


def _toggles(orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> PluginToggles:
    return PluginToggles(
        config=orchestrator.config,
        own_disabled=list(writable_list_field(orchestrator, "disabled_plugins") or []),
    )


def _listed(
    resolution: ResolvedPluginSet, project_root: Path | None
) -> list[InstalledPlugin]:
    """Without a project, the user's own and built-in plugins only."""
    return sorted(
        (
            plugin
            for plugin in resolution.installed
            if project_root is not None
            or plugin.descriptor.scope is not SkillScope.PROJECT
        ),
        key=lambda plugin: plugin.descriptor.name,
    )


def _namespace_rival(
    plugin: InstalledPlugin, resolution: ResolvedPluginSet
) -> str | None:
    """Another plugin that is on under the same namespace: both would drop out of sessions."""
    return next(iter(_namespace_rivals(plugin, resolution)), None)


def _namespace_rivals(
    plugin: InstalledPlugin, resolution: ResolvedPluginSet
) -> list[str]:
    return sorted(
        other.descriptor.name
        for other in resolution.installed
        if other.descriptor.namespace == plugin.descriptor.namespace
        and other.descriptor.name != plugin.descriptor.name
        and other.status != "disabled"
    )


def _summary(
    plugin: InstalledPlugin, resolution: ResolvedPluginSet, toggles: PluginToggles
) -> LocalPluginSummary:
    descriptor = plugin.descriptor
    return LocalPluginSummary(
        name=descriptor.name,
        display_name=descriptor.display_name,
        description=descriptor.description,
        version=descriptor.version,
        author=descriptor.author,
        scope=cast(PluginScope, descriptor.scope.value),
        source_format=descriptor.source_format.value,
        root=str(descriptor.root),
        components=_components(plugin),
        enabled=plugin.status != "disabled",
        locked=toggles.locked(descriptor),
        required=is_required_plugin(descriptor),
        collides_with=_namespace_rivals(plugin, resolution)
        if plugin.status == "collides"
        else [],
    )


def _components(plugin: InstalledPlugin) -> list[LocalPluginComponent]:
    def component(
        kind: PluginComponentKind, label: str, description: str | None
    ) -> LocalPluginComponent:
        return LocalPluginComponent(
            kind=kind, name=label, description=(description or "").strip() or None
        )

    return [
        *(
            component("skill", name, skill.description)
            for name, skill in plugin.skills.items()
        ),
        *(
            component("knowledge", item.display_name or item.name, item.description)
            for item in plugin.knowledge
        ),
        *(
            component(
                "subagent" if agent.profile.agent_type.value == "subagent" else "agent",
                agent.name,
                agent.profile.description,
            )
            for agent in plugin.agents
        ),
        *(
            component("library", library.alias, library.language.capitalize())
            for library in plugin.libraries
        ),
        *(
            component(
                "hook", hook.declared_name or hook.config.name, hook.config.description
            )
            for hook in plugin.runtime_hooks
        ),
        *(
            component("mcp_server", server.source_id, _server_address(server.server))
            for server in plugin.mcp_servers
        ),
        *(
            component("connector", connector.source_id, ", ".join(connector.tools))
            for connector in plugin.connectors
        ),
    ]


def _server_address(server: MCPServer) -> str:
    """The program a local server runs, or the address of a remote one."""
    if not isinstance(server, MCPStdio):
        return server.url
    command = server.command if isinstance(server.command, str) else server.command[0]
    return Path(command).name


def _project_root(value: str | None) -> Path | None:
    if value is None:
        return None
    root = Path(value).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            f"Not a project folder: {value!r}",
            LocalSkillRefusalData(reason="not_a_project_folder").model_dump(
                mode="json"
            ),
        )
    return root.resolve()
