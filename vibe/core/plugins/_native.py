from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import re
import tomllib
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from vibe.agents import AgentSafety, AgentType
from vibe.core.agents.models import AgentProfile
from vibe.core.agents.registry import apply_profile_overrides
from vibe.core.config import (
    MCPServer,
    MCPStaticAuth,
    MCPStdio,
    MCPStreamableHttp,
    VibeConfigSchema,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.mcp_servers import MCPServerAddError, normalize_mcp_server_url
from vibe.core.hooks.config import load_hooks_file
from vibe.core.hooks.models import HookSource, HookVisibility, RuntimeHookDefinition
from vibe.core.paths import VIBE_HOME
from vibe.core.plugins._canonical import canonical_json_digest
from vibe.core.plugins._claude import ClaudePluginAdapter
from vibe.core.plugins._codex import CodexPluginAdapter
from vibe.core.plugins._compatibility import (
    AdaptedHook,
    AdaptedMCPServer,
    AdaptedPluginPackage,
    AdaptedSkill,
    AdaptedUnsupportedComponent,
    DetectedPluginFormat,
    PluginAdapterDiagnostic,
    PluginFormatAdapter,
    PluginToolOverride,
    author_display_name,
    detect_plugin_source_format,
    plugin_runtime_state_names,
    relative_plugin_path,
    typescript_identifier,
)
from vibe.core.plugins._content import digest_plugin_tree
from vibe.core.plugins._foreign import OpenCodePluginAdapter
from vibe.core.plugins._kimi import KimiPluginAdapter
from vibe.core.skills.models import SkillInfo, SkillMetadata, SkillScope, SkillSource
from vibe.core.skills.parser import SkillParseError, parse_skill_markdown
from vibe.utils.io import read_safe
from vibe.utils.platform import is_windows

if TYPE_CHECKING:
    from vibe.core.config.orchestrator import ConfigOrchestrator

logger = logging.getLogger(__name__)

_RESERVED_NAMESPACES = {"file_system", "self", "process", "agent", "vibe"}
_VIBE_EXTENSION = "ai.mistral.vibe"
_TYPESCRIPT_IDENTIFIER_PATTERN = r"^[A-Za-z_$][A-Za-z0-9_$]*$"
_PLUGIN_PLACEHOLDER_PATTERN = re.compile(r"\$\{PLUGIN_(?:ROOT|DATA)\}")
_HTTP_HEADER_NAME_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HTTP_HEADER_MIN_VISIBLE = 0x20
_HTTP_HEADER_DELETE = 0x7F
_HTTP_HEADER_MAX_BYTE = 0xFF
_RUNTIME_SKILL_PATH_METADATA = "unified-harness.runtime-skill-path"
_SKILL_TRANSLATION_METADATA = "unified-harness.translation"
_VIBE_EXTENSION_DIRECTORY = "ai.mistral.vibe"
_MAX_PLUGIN_HOOKS_BYTES = 64 * 1024
_MAX_PLUGIN_HOOKS = 128
_MAX_PLUGIN_KNOWLEDGE_FOLDERS = 100
_MAX_PLUGIN_KNOWLEDGE_ENTRYPOINT_BYTES = 256 * 1024
_MAX_PLUGIN_AGENTS = 128
_MAX_PLUGIN_AGENT_BYTES = 64 * 1024
_MAX_PLUGIN_COMPONENT_PATH_LENGTH = 1024
_MAX_NODE_LIBRARY_ALIAS_LENGTH = 214
_MAX_PYTHON_LIBRARY_ALIAS_LENGTH = 128
_MAX_CONNECTOR_TOOL_NAME_LENGTH = 256
_PLUGIN_COMPONENT_NAME_PATTERN = r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"
_NODE_LIBRARY_ALIAS_PATTERN = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$"
)
_PYTHON_LIBRARY_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class _ManifestAuthor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = None
    email: str | None = None
    url: str | None = None


class _PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    schema_id: Literal["https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"] = (
        Field(alias="$schema")
    )
    name: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$"
    )
    version: str | None = None
    description: str | None = None
    author: _ManifestAuthor | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: list[str] | None = None
    extensions: dict[str, object] | None = None

    @field_validator("name")
    @classmethod
    def reject_repeated_separators(cls, value: str) -> str:
        if "--" in value or ".." in value:
            raise ValueError("plugin names cannot contain '--' or '..'")
        return value


class _ToolOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, pattern=_TYPESCRIPT_IDENTIFIER_PATTERN)
    exposure: Literal["programmatic", "direct", "direct_and_programmatic"] | None = None


class _VibePluginExtension(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    tool_namespace: str | None = Field(
        default=None, alias="toolNamespace", pattern=_TYPESCRIPT_IDENTIFIER_PATTERN
    )
    tool_overrides: dict[str, _ToolOverride] = Field(
        default_factory=dict, alias="toolOverrides"
    )


class _KnowledgeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(
        min_length=1, max_length=64, pattern=_PLUGIN_COMPONENT_NAME_PATTERN
    )
    description: str = Field(min_length=5, max_length=300)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    icon: str | None = Field(default=None, min_length=1, max_length=16)


class _PluginAgentToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    permission: Literal["always", "ask", "never"] | None = None
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)

    @field_validator("allowlist", "denylist")
    @classmethod
    def reject_duplicate_patterns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tool allowlists and denylists cannot contain duplicates")
        return value


class _PluginAgentDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1]
    agent_type: Literal["subagent"]
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=300)
    safety: Literal["safe", "neutral", "destructive", "yolo"] = "neutral"
    active_model: str | None = Field(default=None, min_length=1)
    instructions: str | None = Field(default=None, max_length=65_536)
    enabled_tools: list[str] = Field(default_factory=list, max_length=128)
    disabled_tools: list[str] = Field(default_factory=list, max_length=128)
    tools: dict[str, _PluginAgentToolConfig] = Field(
        default_factory=dict, max_length=128
    )

    @field_validator("enabled_tools", "disabled_tools")
    @classmethod
    def reject_duplicate_tool_names(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("tool lists cannot contain duplicate names")
        if any(not name for name in value):
            raise ValueError("tool names cannot be empty")
        return value


class _PluginLibrariesDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    node: dict[str, str] = Field(default_factory=dict, max_length=128)
    python: dict[str, str] = Field(default_factory=dict, max_length=128)

    @field_validator("node")
    @classmethod
    def validate_node_libraries(cls, value: dict[str, str]) -> dict[str, str]:
        for alias, source in value.items():
            if (
                len(alias) > _MAX_NODE_LIBRARY_ALIAS_LENGTH
                or _NODE_LIBRARY_ALIAS_PATTERN.fullmatch(alias) is None
            ):
                raise ValueError(f"invalid Node package alias: {alias!r}")
            _validate_plugin_relative_path(source, "Node library")
        return value

    @field_validator("python")
    @classmethod
    def validate_python_libraries(cls, value: dict[str, str]) -> dict[str, str]:
        for alias, source in value.items():
            if (
                len(alias) > _MAX_PYTHON_LIBRARY_ALIAS_LENGTH
                or _PYTHON_LIBRARY_ALIAS_PATTERN.fullmatch(alias) is None
            ):
                raise ValueError(f"invalid Python package alias: {alias!r}")
            _validate_plugin_relative_path(source, "Python library")
        return value


class _PluginConnectorRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=256)
    tools: list[str] = Field(min_length=1, max_length=256)

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("connector tool allowlists cannot contain duplicates")
        if any(
            not tool or len(tool) > _MAX_CONNECTOR_TOOL_NAME_LENGTH for tool in value
        ):
            raise ValueError("connector tool names must contain 1 to 256 characters")
        return value


class _PluginConnectorsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    connectors: list[_PluginConnectorRequirement] = Field(max_length=128)

    @field_validator("connectors")
    @classmethod
    def validate_connector_ids(
        cls, value: list[_PluginConnectorRequirement]
    ) -> list[_PluginConnectorRequirement]:
        ids = [connector.id for connector in value]
        if len(ids) != len(set(ids)):
            raise ValueError("connector requirements cannot contain duplicate ids")
        return value


class _MCPConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    schema_id: Literal["https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"] = (
        Field(alias="$schema")
    )
    mcp_servers: dict[str, object] = Field(alias="mcpServers")


class _MCPStdioServer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["stdio"]
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None

    @field_validator("env")
    @classmethod
    def reject_reserved_environment(cls, value: dict[str, str]) -> dict[str, str]:
        reserved = {"PLUGIN_ROOT", "PLUGIN_DATA"}
        collision = sorted(
            name
            for name in value
            if name in reserved or (is_windows() and name.upper() in reserved)
        )
        if collision:
            raise ValueError(
                "env cannot define reserved variables: " + ", ".join(collision)
            )
        return value


class _MCPStreamableHTTPServer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["streamable-http"]
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_http_headers(value)


class _MCPSSEServer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["sse"]
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_http_headers(value)


_MCP_SERVER_ADAPTER = TypeAdapter(
    Annotated[
        _MCPStdioServer | _MCPStreamableHTTPServer | _MCPSSEServer,
        Field(discriminator="type"),
    ]
)


class PluginConfigIssue(BaseModel):
    file: Path
    message: str
    severity: Literal["info", "warning", "error"] = "error"
    code: str | None = None
    fatal: bool = False
    source_format: DetectedPluginFormat | None = None
    component: str | None = None


@dataclass(frozen=True, slots=True)
class PluginDescriptor:
    name: str
    version: str | None
    description: str
    root: Path
    manifest_path: Path
    data_root: Path
    namespace: str
    scope: SkillScope
    source_format: DetectedPluginFormat
    tool_overrides: Mapping[str, PluginToolOverride]
    private_metadata: Mapping[str, object]
    manifest_digest: str
    content_digest: str
    author: str | None = None


@dataclass(frozen=True, slots=True)
class PluginMCPServerDefinition:
    plugin_name: str
    plugin_namespace: str
    source_id: str
    private_alias: str
    server: MCPServer
    config_file: Path


@dataclass(frozen=True, slots=True)
class PluginKnowledgeDefinition:
    plugin_name: str
    name: str
    source_name: str
    description: str
    display_name: str | None
    icon: str | None
    source_root: Path
    source_entrypoint: Path
    runtime_root: Path
    runtime_entrypoint: Path


@dataclass(frozen=True, slots=True)
class PluginAgentDefinition:
    plugin_name: str
    name: str
    source_name: str
    source_file: Path
    profile: AgentProfile


@dataclass(frozen=True, slots=True)
class PluginLibraryDefinition:
    plugin_name: str
    language: Literal["node", "python"]
    alias: str
    source_path: Path
    runtime_path: Path
    config_file: Path


@dataclass(frozen=True, slots=True)
class PluginConnectorDefinition:
    plugin_name: str
    source_id: str
    tools: tuple[str, ...]
    config_file: Path


@dataclass(frozen=True, slots=True)
class ResolvedPluginSet:
    plugins: tuple[PluginDescriptor, ...]
    skills: Mapping[str, SkillInfo]
    mcp_servers: tuple[PluginMCPServerDefinition, ...]
    runtime_hooks: tuple[RuntimeHookDefinition, ...]
    knowledge: tuple[PluginKnowledgeDefinition, ...]
    agents: tuple[PluginAgentDefinition, ...]
    libraries: tuple[PluginLibraryDefinition, ...]
    connectors: tuple[PluginConnectorDefinition, ...]
    issues: tuple[PluginConfigIssue, ...]
    unsupported_components: tuple[PluginUnsupportedComponent, ...] = ()

    @classmethod
    def empty(cls) -> ResolvedPluginSet:
        return cls(
            plugins=(),
            skills=MappingProxyType({}),
            mcp_servers=(),
            runtime_hooks=(),
            knowledge=(),
            agents=(),
            libraries=(),
            connectors=(),
            issues=(),
        )


@dataclass(frozen=True, slots=True)
class PluginUnsupportedComponent:
    plugin_name: str
    source_format: DetectedPluginFormat
    kind: str
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class _PluginCandidate:
    descriptor: PluginDescriptor
    skills: Mapping[str, SkillInfo]
    mcp_servers: tuple[PluginMCPServerDefinition, ...]
    runtime_hooks: tuple[RuntimeHookDefinition, ...]
    knowledge: tuple[PluginKnowledgeDefinition, ...]
    agents: tuple[PluginAgentDefinition, ...]
    libraries: tuple[PluginLibraryDefinition, ...]
    connectors: tuple[PluginConnectorDefinition, ...]
    manifest_path: Path


class PluginResolver:
    def __init__(
        self,
        *,
        project_roots: Sequence[Path] = (),
        user_roots: Sequence[Path] = (),
        plugin_dirs: Sequence[Path] = (),
        data_root_base: Path | None = None,
        config_orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None,
    ) -> None:
        self._project_roots = tuple(project_roots)
        self._user_roots = tuple(user_roots)
        # Named outright rather than discovered under a root: a pinned session
        # rebuilding its recorded set has nothing to scan and no scope to
        # distinguish, so these join the project scope and skip discovery.
        self._plugin_dirs = tuple(plugin_dirs)
        self._data_root_base = data_root_base
        self._config_orchestrator = config_orchestrator
        self._issues: list[PluginConfigIssue] = []
        self._unsupported_components: list[PluginUnsupportedComponent] = []

    @classmethod
    def from_harness_files(
        cls,
        harness_files: HarnessFilesManager,
        *,
        config_orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None,
    ) -> PluginResolver:
        return cls(
            project_roots=harness_files.project_plugins_dirs,
            user_roots=harness_files.user_plugins_dirs,
            config_orchestrator=config_orchestrator,
        )

    def resolve(self) -> ResolvedPluginSet:
        self._issues = []
        self._unsupported_components = []
        project = self._load_scope(
            [*self._plugin_dirs, *self._child_dirs(self._project_roots)],
            SkillScope.PROJECT,
        )
        user = self._load_scope(self._child_dirs(self._user_roots), SkillScope.GLOBAL)
        selected = self._select_by_precedence(project, user)
        selected = self._remove_namespace_collisions(selected)
        plugins = tuple(
            sorted(
                (candidate.descriptor for candidate in selected), key=lambda p: p.name
            )
        )
        skills = {
            name: skill
            for candidate in sorted(selected, key=lambda item: item.descriptor.name)
            for name, skill in candidate.skills.items()
        }
        mcp_servers = tuple(
            server
            for candidate in sorted(selected, key=lambda item: item.descriptor.name)
            for server in candidate.mcp_servers
        )
        runtime_hooks = tuple(
            hook
            for candidate in sorted(
                selected,
                key=lambda item: (
                    0 if item.descriptor.scope is SkillScope.PROJECT else 1,
                    item.descriptor.name,
                ),
            )
            for hook in candidate.runtime_hooks
        )
        knowledge = tuple(
            definition
            for candidate in sorted(selected, key=lambda item: item.descriptor.name)
            for definition in candidate.knowledge
        )
        agents = tuple(
            definition
            for candidate in sorted(selected, key=lambda item: item.descriptor.name)
            for definition in candidate.agents
        )
        libraries = self._remove_library_collisions(
            tuple(
                definition
                for candidate in sorted(selected, key=lambda item: item.descriptor.name)
                for definition in candidate.libraries
            )
        )
        connectors = tuple(
            definition
            for candidate in sorted(selected, key=lambda item: item.descriptor.name)
            for definition in candidate.connectors
        )
        if plugins:
            logger.debug(
                "Resolved %d plugin(s) [%s]: skills=%d mcp_servers=%d hooks=%d "
                "knowledge=%d agents=%d libraries=%d connectors=%d issues=%d",
                len(plugins),
                ", ".join(_plugin_log_label(plugin) for plugin in plugins),
                len(skills),
                len(mcp_servers),
                len(runtime_hooks),
                len(knowledge),
                len(agents),
                len(libraries),
                len(connectors),
                len(self._issues),
            )
        return ResolvedPluginSet(
            plugins=plugins,
            skills=MappingProxyType(skills),
            mcp_servers=mcp_servers,
            runtime_hooks=runtime_hooks,
            knowledge=knowledge,
            agents=agents,
            libraries=libraries,
            connectors=connectors,
            issues=tuple(self._issues),
            unsupported_components=tuple(self._unsupported_components),
        )

    def _child_dirs(self, roots: Sequence[Path]) -> list[Path]:
        """Every plugin directory sitting under one of these roots."""
        plugin_dirs: list[Path] = []
        for root in roots:
            try:
                plugin_dirs.extend(
                    sorted(
                        (path for path in root.iterdir() if path.is_dir()),
                        key=lambda path: path.name,
                    )
                )
            except OSError as error:
                self._issues.append(
                    PluginConfigIssue(file=root, message=f"Failed to discover: {error}")
                )
        return plugin_dirs

    def _load_scope(
        self, plugin_dirs: Sequence[Path], scope: SkillScope
    ) -> list[_PluginCandidate]:
        candidates = [
            candidate
            for plugin_dir in plugin_dirs
            if (candidate := self._load_plugin(plugin_dir, scope))
        ]
        return self._remove_same_scope_duplicates(candidates)

    def _load_plugin(
        self, plugin_dir: Path, scope: SkillScope
    ) -> _PluginCandidate | None:
        try:
            root = plugin_dir.resolve(strict=True)
        except OSError as error:
            self._issues.append(
                PluginConfigIssue(
                    file=plugin_dir, message=f"Failed to resolve: {error}"
                )
            )
            return None

        detection = detect_plugin_source_format(root)
        if detection.unsupported_schema is not None:
            self._issues.append(
                PluginConfigIssue(
                    file=root / "plugin.json",
                    message=(
                        "Agent Plugins schema version is not supported: "
                        f"{detection.unsupported_schema}"
                    ),
                    code="plugin.schema.version_unsupported",
                    fatal=True,
                    source_format=DetectedPluginFormat.AGENT_PLUGINS_1_0,
                    component="manifest",
                )
            )
            return None
        if detection.source_format is DetectedPluginFormat.AGENT_PLUGINS_1_0:
            return self._load_native_plugin(root, scope)
        adapter = _compatibility_adapter(detection.source_format)
        if adapter is not None:
            result = adapter.adapt(
                root=root, data_root_base=self._plugin_data_root_base(root), scope=scope
            )
            self._record_adapter_result(detection.source_format, result.diagnostics)
            if result.package is None:
                self._record_unsupported_components(
                    root.name, detection.source_format, result.unsupported_components
                )
                return None
            return self._candidate_from_adapted(
                root, result.package, result.unsupported_components
            )

        marker_paths = ", ".join(
            relative_plugin_path(path, root) for path in detection.marker_paths
        )
        if detection.source_format is DetectedPluginFormat.AMBIGUOUS:
            message = f"Multiple plugin formats were detected: {marker_paths}"
            code = "plugin.compatibility.format_ambiguous"
        elif detection.source_format is DetectedPluginFormat.OPENCODE:
            message = "Executable OpenCode plugins are not imported or evaluated"
            code = "plugin.compatibility.executable_format_unsupported"
        elif detection.source_format is DetectedPluginFormat.UNKNOWN:
            message = "No supported plugin manifest was found"
            code = "plugin.compatibility.format_unrecognized"
        else:
            message = (
                f"The {detection.source_format.value} plugin format was detected, "
                "but its compatibility adapter is not available"
            )
            code = "plugin.compatibility.adapter_unavailable"
        self._issues.append(
            PluginConfigIssue(
                file=detection.marker_paths[0] if detection.marker_paths else root,
                message=message,
                code=code,
                fatal=True,
                source_format=detection.source_format,
                component="manifest",
            )
        )
        return None

    def _load_native_plugin(
        self, root: Path, scope: SkillScope
    ) -> _PluginCandidate | None:
        manifest_path = root / "plugin.json"
        try:
            resolved_manifest = manifest_path.resolve(strict=True)
            if (
                not resolved_manifest.is_relative_to(root)
                or not resolved_manifest.is_file()
            ):
                raise ValueError(
                    "plugin.json must resolve to a regular file inside the plugin root"
                )
            manifest = self._parse_manifest(resolved_manifest)
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            self._issues.append(
                PluginConfigIssue(
                    file=manifest_path, message=f"Failed to load: {error}"
                )
            )
            return None

        extension = self._parse_vibe_extension(manifest, resolved_manifest)
        try:
            manifest_digest = canonical_json_digest(manifest)
            content_digest = digest_plugin_tree(
                root,
                ignored_names=plugin_runtime_state_names(
                    DetectedPluginFormat.AGENT_PLUGINS_1_0
                ),
            )
        except OSError as error:
            self._issues.append(
                PluginConfigIssue(
                    file=root, message=f"Failed to digest plugin contents: {error}"
                )
            )
            return None
        namespace = (
            extension.tool_namespace
            if extension is not None and extension.tool_namespace is not None
            else _typescript_identifier(manifest.name)
        )
        if namespace in _RESERVED_NAMESPACES:
            self._issues.append(
                PluginConfigIssue(
                    file=manifest_path,
                    message=f"Plugin namespace {namespace!r} is reserved",
                )
            )
            return None
        descriptor = PluginDescriptor(
            name=manifest.name,
            version=manifest.version,
            description=manifest.description
            or f"Capabilities provided by {manifest.name}.",
            root=root,
            manifest_path=resolved_manifest,
            data_root=self._plugin_data_root_base(root) / manifest.name,
            namespace=namespace,
            scope=scope,
            source_format=DetectedPluginFormat.AGENT_PLUGINS_1_0,
            tool_overrides=MappingProxyType({
                key: PluginToolOverride(name=value.name, exposure=value.exposure)
                for key, value in (
                    extension.tool_overrides.items() if extension is not None else ()
                )
            }),
            private_metadata=MappingProxyType({}),
            manifest_digest=manifest_digest,
            content_digest=content_digest,
            author=author_display_name(
                manifest.author.name if manifest.author is not None else None
            ),
        )
        skills = self._load_skills(descriptor)
        mcp_servers = self._load_mcp_servers(descriptor)
        runtime_hooks = self._load_runtime_hooks(descriptor, extension)
        knowledge = self._load_knowledge(descriptor, extension)
        agents = self._load_agents(descriptor, extension)
        return _PluginCandidate(
            descriptor=descriptor,
            skills=MappingProxyType(skills),
            mcp_servers=mcp_servers,
            runtime_hooks=runtime_hooks,
            knowledge=knowledge,
            agents=agents,
            libraries=self._load_libraries(descriptor, extension),
            connectors=self._load_connectors(descriptor, extension),
            manifest_path=resolved_manifest,
        )

    def _candidate_from_adapted(
        self,
        root: Path,
        package: AdaptedPluginPackage,
        unsupported: Sequence[AdaptedUnsupportedComponent],
    ) -> _PluginCandidate | None:
        if package.namespace in _RESERVED_NAMESPACES:
            self._issues.append(
                PluginConfigIssue(
                    file=package.manifest_path,
                    message=f"Plugin namespace {package.namespace!r} is reserved",
                    code="plugin.namespace.reserved",
                    fatal=True,
                    source_format=package.source_format,
                    component="manifest",
                )
            )
            return None
        try:
            manifest_text = read_safe(package.manifest_path, raise_on_error=True).text
            content_digest = digest_plugin_tree(
                root, ignored_names=plugin_runtime_state_names(package.source_format)
            )
        except OSError as error:
            self._issues.append(
                PluginConfigIssue(
                    file=package.manifest_path,
                    message=f"Failed to digest plugin contents: {error}",
                    code="plugin.filesystem.error",
                    fatal=True,
                    source_format=package.source_format,
                    component="package",
                )
            )
            return None
        try:
            manifest_digest = canonical_json_digest(json.loads(manifest_text))
        except json.JSONDecodeError:
            manifest_digest = hashlib.sha256(manifest_text.encode()).hexdigest()

        descriptor = PluginDescriptor(
            name=package.name,
            version=package.version,
            description=package.description,
            root=root,
            manifest_path=package.manifest_path,
            data_root=package.data_root,
            namespace=package.namespace,
            scope=package.scope,
            source_format=package.source_format,
            tool_overrides=package.tool_overrides,
            private_metadata=package.private_metadata,
            manifest_digest=manifest_digest,
            content_digest=content_digest,
            author=package.author,
        )
        self._unsupported_components.extend(
            PluginUnsupportedComponent(
                plugin_name=package.name,
                source_format=package.source_format,
                kind=component.kind,
                path=component.path,
                reason=component.reason,
            )
            for component in unsupported
        )
        skills = self._load_skills_from_roots(descriptor, package.skill_roots)
        skills.update(self._load_adapted_skills(descriptor, package.adapted_skills))
        return _PluginCandidate(
            descriptor=descriptor,
            skills=MappingProxyType(skills),
            mcp_servers=self._adapt_mcp_servers(descriptor, package.mcp_servers),
            runtime_hooks=self._adapt_runtime_hooks(descriptor, package.adapted_hooks),
            knowledge=(),
            agents=(),
            libraries=(),
            connectors=(),
            manifest_path=package.manifest_path,
        )

    def _record_unsupported_components(
        self,
        plugin_name: str,
        source_format: DetectedPluginFormat,
        unsupported: Sequence[AdaptedUnsupportedComponent],
    ) -> None:
        self._unsupported_components.extend(
            PluginUnsupportedComponent(
                plugin_name=plugin_name,
                source_format=source_format,
                kind=component.kind,
                path=component.path,
                reason=component.reason,
            )
            for component in unsupported
        )

    def _load_adapted_skills(
        self, plugin: PluginDescriptor, definitions: Sequence[AdaptedSkill]
    ) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {}
        for definition in definitions:
            alias = f"{plugin.namespace}:{definition.source_name}"
            if alias in skills:
                self._issues.append(
                    PluginConfigIssue(
                        file=definition.source_path,
                        message=f"Duplicate plugin skill alias {alias!r}",
                        code="plugin.skill.collision",
                        fatal=False,
                        source_format=plugin.source_format,
                        component="skill",
                    )
                )
                continue
            runtime_path = definition.source_path
            if definition.translation == "synthetic_skill":
                runtime_path = (
                    plugin.data_root
                    / "generated-skills"
                    / definition.source_name
                    / "SKILL.md"
                )
                try:
                    runtime_path.parent.mkdir(parents=True, exist_ok=True)
                    runtime_path.write_text(
                        _render_generated_skill(definition), encoding="utf-8"
                    )
                except OSError as error:
                    self._issues.append(
                        PluginConfigIssue(
                            file=definition.source_path,
                            message=f"Failed to materialize generated skill: {error}",
                            code="plugin.skill.materialization_failed",
                            fatal=False,
                            source_format=plugin.source_format,
                            component="skill",
                        )
                    )
                    continue
            metadata = dict(definition.metadata)
            metadata[_RUNTIME_SKILL_PATH_METADATA] = str(runtime_path)
            if definition.translation is not None:
                metadata[_SKILL_TRANSLATION_METADATA] = definition.translation
            skill_metadata = SkillMetadata(
                name=definition.source_name,
                description=definition.description,
                license=definition.license,
                compatibility=definition.compatibility,
                metadata=metadata,
                allowed_tools=list(definition.allowed_tools),
                user_invocable=definition.user_invocable,
            )
            skill = SkillInfo.from_metadata(
                skill_metadata,
                definition.source_path,
                definition.prompt,
                scope=plugin.scope,
            )
            skills[alias] = skill.model_copy(
                update={"name": alias, "source": SkillSource.PLUGIN}
            )
        return skills

    @staticmethod
    def _adapt_runtime_hooks(
        plugin: PluginDescriptor, definitions: Sequence[AdaptedHook]
    ) -> tuple[RuntimeHookDefinition, ...]:
        source = (
            HookSource.PROJECT_PLUGIN
            if plugin.scope is SkillScope.PROJECT
            else HookSource.GLOBAL_PLUGIN
        )
        environment = {
            "PLUGIN_ROOT": str(plugin.root),
            "PLUGIN_DATA": str(plugin.data_root),
        }
        cwd: Path | None = plugin.root
        if plugin.source_format is DetectedPluginFormat.CLAUDE_CODE:
            environment["CLAUDE_PLUGIN_ROOT"] = str(plugin.root)
            environment["CLAUDE_PLUGIN_DATA"] = str(plugin.data_root)
            cwd = None
        if plugin.source_format is DetectedPluginFormat.KIMI_CODE:
            environment["KIMI_PLUGIN_ROOT"] = str(plugin.root)
            environment["KIMI_CODE_HOME"] = str(VIBE_HOME.path)
        return tuple(
            RuntimeHookDefinition(
                config=definition.config.model_copy(
                    update={"name": f"{plugin.name}:{definition.config.name}"}
                ),
                source=source,
                order=order,
                cwd=cwd,
                environment=environment,
                visibility=HookVisibility.PRIVATE,
                protocol=definition.protocol,
                plugin_name=plugin.name,
                declared_name=definition.config.name,
                config_file=definition.source_path,
            )
            for order, definition in enumerate(definitions)
        )

    def _record_adapter_result(
        self,
        source_format: DetectedPluginFormat,
        diagnostics: Sequence[PluginAdapterDiagnostic],
    ) -> None:
        self._issues.extend(
            PluginConfigIssue(
                file=diagnostic.path,
                message=diagnostic.message,
                severity=diagnostic.severity,
                code=diagnostic.code,
                fatal=diagnostic.fatal,
                source_format=source_format,
                component=diagnostic.component,
            )
            for diagnostic in diagnostics
        )

    def _plugin_data_root_base(self, root: Path) -> Path:
        return self._data_root_base or root.parent.parent / "plugin-data"

    @staticmethod
    def _adapt_mcp_servers(
        plugin: PluginDescriptor, servers: Sequence[AdaptedMCPServer]
    ) -> tuple[PluginMCPServerDefinition, ...]:
        definitions = []
        for adapted in servers:
            private_alias = _private_server_alias(plugin, adapted.source_id)
            definitions.append(
                PluginMCPServerDefinition(
                    plugin_name=plugin.name,
                    plugin_namespace=plugin.namespace,
                    source_id=adapted.source_id,
                    private_alias=private_alias,
                    server=adapted.server.model_copy(update={"name": private_alias}),
                    config_file=adapted.config_file,
                )
            )
        return tuple(definitions)

    def _parse_manifest(self, path: Path) -> _PluginManifest:
        raw = json.loads(read_safe(path, raise_on_error=True).text)
        return _PluginManifest.model_validate(raw)

    def _parse_vibe_extension(
        self, manifest: _PluginManifest, manifest_path: Path
    ) -> _VibePluginExtension | None:
        extensions = manifest.extensions or {}
        raw = extensions.get(_VIBE_EXTENSION)
        if raw is None:
            return None
        try:
            return _VibePluginExtension.model_validate(raw)
        except ValidationError as error:
            self._issues.append(
                PluginConfigIssue(
                    file=manifest_path,
                    message=f"Failed to load {_VIBE_EXTENSION} extension: {error}",
                )
            )
            return None

    def _load_runtime_hooks(
        self, plugin: PluginDescriptor, extension: _VibePluginExtension | None
    ) -> tuple[RuntimeHookDefinition, ...]:
        if extension is None:
            return ()
        path = plugin.root / _VIBE_EXTENSION_DIRECTORY / "hooks.toml"
        if not path.exists() and not path.is_symlink():
            return ()
        try:
            resolved = _contained_component_file(plugin.root, path)
            if resolved.stat().st_size > _MAX_PLUGIN_HOOKS_BYTES:
                raise ValueError("hooks.toml exceeds the 65536-byte plugin hook limit")
        except (OSError, ValueError) as error:
            self._issues.append(
                PluginConfigIssue(
                    file=path,
                    message=f"Failed to load plugin hooks: {error}",
                    code="plugin.hooks.invalid",
                    source_format=plugin.source_format,
                    component="hook",
                )
            )
            return ()

        parsed = load_hooks_file(resolved, strict=True)
        self._issues.extend(
            PluginConfigIssue(
                file=issue.file,
                message=issue.message,
                code="plugin.hooks.invalid",
                source_format=plugin.source_format,
                component="hook",
            )
            for issue in parsed.issues
        )
        source = (
            HookSource.PROJECT_PLUGIN
            if plugin.scope is SkillScope.PROJECT
            else HookSource.GLOBAL_PLUGIN
        )
        environment = {
            "PLUGIN_ROOT": str(plugin.root),
            "PLUGIN_DATA": str(plugin.data_root),
        }
        definitions: list[RuntimeHookDefinition] = []
        seen_names: set[str] = set()
        for order, hook in enumerate(parsed.hooks):
            if order >= _MAX_PLUGIN_HOOKS:
                self._issues.append(
                    PluginConfigIssue(
                        file=resolved,
                        message="hooks.toml exceeds the 128-hook plugin limit",
                        code="plugin.hooks.limit_exceeded",
                        source_format=plugin.source_format,
                        component="hook",
                    )
                )
                break
            if hook.name in seen_names:
                self._issues.append(
                    PluginConfigIssue(
                        file=resolved,
                        message=f"Duplicate plugin hook name: {hook.name!r}",
                        code="plugin.hooks.duplicate_name",
                        source_format=plugin.source_format,
                        component="hook",
                    )
                )
                continue
            seen_names.add(hook.name)
            definitions.append(
                RuntimeHookDefinition(
                    config=hook.model_copy(
                        update={"name": f"{plugin.name}:{hook.name}"}
                    ),
                    source=source,
                    order=order,
                    cwd=plugin.root,
                    environment=environment,
                    visibility=HookVisibility.PRIVATE,
                    plugin_name=plugin.name,
                    declared_name=hook.name,
                    config_file=resolved,
                )
            )
        return tuple(definitions)

    def _load_knowledge(
        self, plugin: PluginDescriptor, extension: _VibePluginExtension | None
    ) -> tuple[PluginKnowledgeDefinition, ...]:
        if extension is None:
            return ()
        knowledge_root = plugin.root / _VIBE_EXTENSION_DIRECTORY / "knowledge"
        if not knowledge_root.exists() and not knowledge_root.is_symlink():
            return ()
        try:
            resolved_root = _contained_component_directory(plugin.root, knowledge_root)
            children = sorted(resolved_root.iterdir(), key=lambda path: path.name)
        except (OSError, ValueError) as error:
            self._issues.append(
                PluginConfigIssue(
                    file=knowledge_root,
                    message=f"Failed to load plugin knowledge: {error}",
                    code="plugin.knowledge.invalid",
                    source_format=plugin.source_format,
                    component="knowledge",
                )
            )
            return ()

        definitions: list[PluginKnowledgeDefinition] = []
        for child in children:
            if len(definitions) >= _MAX_PLUGIN_KNOWLEDGE_FOLDERS:
                self._issues.append(
                    PluginConfigIssue(
                        file=resolved_root,
                        message=(
                            "Plugin knowledge exceeds the 100-folder component limit"
                        ),
                        code="plugin.knowledge.limit_exceeded",
                        source_format=plugin.source_format,
                        component="knowledge",
                    )
                )
                break
            if not child.is_dir() and not child.is_symlink():
                continue
            entrypoint = child / "KNOWLEDGE.md"
            try:
                source_root = _contained_component_directory(plugin.root, child)
                _validate_contained_knowledge_tree(plugin.root, source_root)
                source_entrypoint = _contained_component_file(plugin.root, entrypoint)
                if (
                    source_entrypoint.stat().st_size
                    > _MAX_PLUGIN_KNOWLEDGE_ENTRYPOINT_BYTES
                ):
                    raise ValueError(
                        "KNOWLEDGE.md exceeds the 262144-byte entrypoint limit"
                    )
                content = read_safe(source_entrypoint, raise_on_error=True).text
                frontmatter, _ = parse_skill_markdown(content)
                metadata = _KnowledgeMetadata.model_validate(frontmatter)
                if metadata.name != child.name:
                    raise ValueError(
                        f"knowledge name {metadata.name!r} must match directory "
                        f"{child.name!r}"
                    )
            except (OSError, ValueError, ValidationError, SkillParseError) as error:
                self._issues.append(
                    PluginConfigIssue(
                        file=entrypoint,
                        message=f"Failed to load plugin knowledge: {error}",
                        code="plugin.knowledge.invalid",
                        source_format=plugin.source_format,
                        component="knowledge",
                    )
                )
                continue
            runtime_root = plugin.data_root / "knowledge" / metadata.name
            definitions.append(
                PluginKnowledgeDefinition(
                    plugin_name=plugin.name,
                    name=f"{plugin.namespace}:{metadata.name}",
                    source_name=metadata.name,
                    description=metadata.description,
                    display_name=metadata.display_name,
                    icon=metadata.icon,
                    source_root=source_root,
                    source_entrypoint=source_entrypoint,
                    runtime_root=runtime_root,
                    runtime_entrypoint=runtime_root / "KNOWLEDGE.md",
                )
            )
        return tuple(definitions)

    def _load_agents(
        self, plugin: PluginDescriptor, extension: _VibePluginExtension | None
    ) -> tuple[PluginAgentDefinition, ...]:
        if extension is None:
            return ()
        agents_root = plugin.root / _VIBE_EXTENSION_DIRECTORY / "agents"
        if not agents_root.exists() and not agents_root.is_symlink():
            return ()
        try:
            resolved_root = _contained_component_directory(plugin.root, agents_root)
            children = sorted(resolved_root.iterdir(), key=lambda path: path.name)
        except (OSError, ValueError) as error:
            self._issues.append(
                PluginConfigIssue(
                    file=agents_root,
                    message=f"Failed to load plugin agents: {error}",
                    code="plugin.agent.invalid",
                    source_format=plugin.source_format,
                    component="agent",
                )
            )
            return ()

        definitions: list[PluginAgentDefinition] = []
        for child in children:
            if len(definitions) >= _MAX_PLUGIN_AGENTS:
                self._issues.append(
                    PluginConfigIssue(
                        file=resolved_root,
                        message="Plugin agents exceed the 128-agent component limit",
                        code="plugin.agent.limit_exceeded",
                        source_format=plugin.source_format,
                        component="agent",
                    )
                )
                break
            if child.suffix != ".toml":
                continue
            try:
                source_file = _contained_component_file(plugin.root, child)
                if source_file.stat().st_size > _MAX_PLUGIN_AGENT_BYTES:
                    raise ValueError(
                        "agent TOML exceeds the 65536-byte component limit"
                    )
                if not re.fullmatch(_PLUGIN_COMPONENT_NAME_PATTERN, child.stem):
                    raise ValueError(
                        "agent filename must be a lowercase kebab-case name"
                    )
                document = _PluginAgentDocument.model_validate(
                    tomllib.loads(read_safe(source_file, raise_on_error=True).text)
                )
            except (
                OSError,
                ValueError,
                ValidationError,
                tomllib.TOMLDecodeError,
            ) as error:
                self._issues.append(
                    PluginConfigIssue(
                        file=child,
                        message=f"Failed to load plugin agent: {error}",
                        code="plugin.agent.invalid",
                        source_format=plugin.source_format,
                        component="agent",
                    )
                )
                continue
            name = f"{plugin.namespace}:{child.stem}"
            overrides: dict[str, object] = {}
            if document.active_model is not None:
                overrides["active_model"] = document.active_model
            if document.enabled_tools:
                overrides["enabled_tools"] = document.enabled_tools
            if document.disabled_tools:
                overrides["disabled_tools"] = document.disabled_tools
            if document.tools:
                overrides["tools"] = {
                    tool_name: config.model_dump(exclude_none=True)
                    for tool_name, config in document.tools.items()
                }
            profile = AgentProfile(
                name=name,
                display_name=document.display_name
                or child.stem.replace("-", " ").title(),
                description=document.description,
                safety=AgentSafety(document.safety),
                agent_type=AgentType.SUBAGENT,
                overrides=overrides,
                instructions=document.instructions,
            )
            if self._config_orchestrator is not None:
                try:
                    candidate = self._config_orchestrator.copy()
                    apply_profile_overrides(candidate, profile.overrides)
                except (ValidationError, ValueError) as error:
                    self._issues.append(
                        PluginConfigIssue(
                            file=source_file,
                            message=f"Failed to apply plugin agent: {error}",
                            code="plugin.agent.invalid",
                            source_format=plugin.source_format,
                            component="agent",
                        )
                    )
                    continue
            definitions.append(
                PluginAgentDefinition(
                    plugin_name=plugin.name,
                    name=name,
                    source_name=child.stem,
                    source_file=source_file,
                    profile=profile,
                )
            )
        return tuple(definitions)

    def _load_libraries(
        self, plugin: PluginDescriptor, extension: _VibePluginExtension | None
    ) -> tuple[PluginLibraryDefinition, ...]:
        if extension is None:
            return ()
        config_file = plugin.root / "libraries.json"
        if not config_file.exists() and not config_file.is_symlink():
            return ()
        try:
            resolved_config = _contained_component_file(plugin.root, config_file)
            document = _PluginLibrariesDocument.model_validate_json(
                read_safe(resolved_config, raise_on_error=True).text
            )
        except (OSError, ValueError, ValidationError) as error:
            self._issues.append(
                PluginConfigIssue(
                    file=config_file,
                    message=f"Failed to load plugin libraries: {error}",
                    code="plugin.libraries.invalid",
                    source_format=plugin.source_format,
                    component="library",
                )
            )
            return ()

        definitions: list[PluginLibraryDefinition] = []
        for alias, source in sorted(document.node.items()):
            candidate = plugin.root / source.removeprefix("./")
            try:
                source_path = _contained_component_directory(plugin.root, candidate)
                _validate_contained_library_tree(plugin.root, candidate, source_path)
            except (OSError, ValueError) as error:
                self._issues.append(
                    PluginConfigIssue(
                        file=candidate,
                        message=f"Failed to load Node plugin library {alias!r}: {error}",
                        code="plugin.library.invalid",
                        source_format=plugin.source_format,
                        component="library",
                    )
                )
                continue
            definitions.append(
                PluginLibraryDefinition(
                    plugin_name=plugin.name,
                    language="node",
                    alias=alias,
                    source_path=source_path,
                    runtime_path=(
                        plugin.data_root
                        / "libraries"
                        / plugin.content_digest
                        / "node"
                        / "node_modules"
                        / Path(*alias.split("/"))
                    ),
                    config_file=resolved_config,
                )
            )

        for alias, source in sorted(document.python.items()):
            candidate = plugin.root / source.removeprefix("./")
            try:
                if candidate.is_symlink():
                    raise ValueError("library paths cannot be symbolic links")
                if candidate.is_dir():
                    source_path = _contained_component_directory(plugin.root, candidate)
                    _validate_contained_library_tree(
                        plugin.root, candidate, source_path
                    )
                    runtime_name = alias
                else:
                    source_path = _contained_component_file(plugin.root, candidate)
                    if source_path.suffix != ".py":
                        raise ValueError(
                            "Python library files must use the .py extension"
                        )
                    runtime_name = f"{alias}.py"
            except (OSError, ValueError) as error:
                self._issues.append(
                    PluginConfigIssue(
                        file=candidate,
                        message=(
                            f"Failed to load Python plugin library {alias!r}: {error}"
                        ),
                        code="plugin.library.invalid",
                        source_format=plugin.source_format,
                        component="library",
                    )
                )
                continue
            definitions.append(
                PluginLibraryDefinition(
                    plugin_name=plugin.name,
                    language="python",
                    alias=alias,
                    source_path=source_path,
                    runtime_path=(
                        plugin.data_root
                        / "libraries"
                        / plugin.content_digest
                        / "python"
                        / runtime_name
                    ),
                    config_file=resolved_config,
                )
            )
        return tuple(definitions)

    def _load_connectors(
        self, plugin: PluginDescriptor, extension: _VibePluginExtension | None
    ) -> tuple[PluginConnectorDefinition, ...]:
        if extension is None:
            return ()
        config_file = plugin.root / "connectors.json"
        if not config_file.exists() and not config_file.is_symlink():
            return ()
        try:
            resolved_config = _contained_component_file(plugin.root, config_file)
            document = _PluginConnectorsDocument.model_validate_json(
                read_safe(resolved_config, raise_on_error=True).text
            )
        except (OSError, ValueError, ValidationError) as error:
            self._issues.append(
                PluginConfigIssue(
                    file=config_file,
                    message=f"Failed to load plugin connectors: {error}",
                    code="plugin.connectors.invalid",
                    source_format=plugin.source_format,
                    component="connector",
                )
            )
            return ()
        return tuple(
            PluginConnectorDefinition(
                plugin_name=plugin.name,
                source_id=requirement.id,
                tools=tuple(requirement.tools),
                config_file=resolved_config,
            )
            for requirement in document.connectors
        )

    def _remove_library_collisions(
        self, definitions: tuple[PluginLibraryDefinition, ...]
    ) -> tuple[PluginLibraryDefinition, ...]:
        by_alias: dict[
            tuple[Literal["node", "python"], str], list[PluginLibraryDefinition]
        ] = defaultdict(list)
        for definition in definitions:
            by_alias[(definition.language, definition.alias)].append(definition)
        selected: list[PluginLibraryDefinition] = []
        for (language, alias), matches in sorted(by_alias.items()):
            if len(matches) == 1:
                selected.append(matches[0])
                continue
            plugins = ", ".join(sorted(match.plugin_name for match in matches))
            for match in matches:
                self._issues.append(
                    PluginConfigIssue(
                        file=match.config_file,
                        message=(
                            f"Plugin {language} library alias {alias!r} collides "
                            f"between {plugins}"
                        ),
                        code="plugin.library.alias_collision",
                        source_format=DetectedPluginFormat.AGENT_PLUGINS_1_0,
                        component="library",
                    )
                )
        return tuple(selected)

    def _load_mcp_servers(
        self, plugin: PluginDescriptor
    ) -> tuple[PluginMCPServerDefinition, ...]:
        config_path = plugin.root / "mcp.json"
        if not config_path.exists() and not config_path.is_symlink():
            return ()
        try:
            resolved_config = config_path.resolve(strict=True)
            if (
                not resolved_config.is_relative_to(plugin.root)
                or not resolved_config.is_file()
            ):
                raise ValueError(
                    "mcp.json must resolve to a regular file inside the plugin root"
                )
            raw = json.loads(read_safe(resolved_config, raise_on_error=True).text)
            config = _MCPConfiguration.model_validate(raw)
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as error:
            self._issues.append(
                PluginConfigIssue(
                    file=config_path,
                    message=f"Failed to load MCP configuration: {error}",
                )
            )
            return ()

        servers: list[PluginMCPServerDefinition] = []
        for source_id, raw_server in config.mcp_servers.items():
            try:
                parsed = _MCP_SERVER_ADAPTER.validate_python(raw_server)
                server = self._to_vibe_mcp_server(plugin, source_id, parsed)
            except (MCPServerAddError, OSError, ValueError, ValidationError) as error:
                self._issues.append(
                    PluginConfigIssue(
                        file=resolved_config,
                        message=f"Failed to load MCP server {source_id!r}: {error}",
                    )
                )
                continue
            if server is None:
                self._issues.append(
                    PluginConfigIssue(
                        file=resolved_config,
                        message=(
                            f"MCP server {source_id!r} uses unsupported transport "
                            f"{parsed.type!r}"
                        ),
                    )
                )
                continue
            private_alias = _private_server_alias(plugin, source_id)
            servers.append(
                PluginMCPServerDefinition(
                    plugin_name=plugin.name,
                    plugin_namespace=plugin.namespace,
                    source_id=source_id,
                    private_alias=private_alias,
                    server=server.model_copy(update={"name": private_alias}),
                    config_file=resolved_config,
                )
            )
        return tuple(servers)

    @staticmethod
    def _to_vibe_mcp_server(
        plugin: PluginDescriptor,
        source_id: str,
        parsed: _MCPStdioServer | _MCPStreamableHTTPServer | _MCPSSEServer,
    ) -> MCPServer | None:
        private_alias = _private_server_alias(plugin, source_id)
        match parsed:
            case _MCPStdioServer():
                command = _resolve_plugin_command(plugin.root, parsed.command)
                args = [
                    _expand_plugin_variables(value, plugin.root, plugin.data_root)
                    for value in parsed.args
                ]
                env = {
                    name: _expand_plugin_variables(value, plugin.root, plugin.data_root)
                    for name, value in parsed.env.items()
                }
                env["PLUGIN_ROOT"] = str(plugin.root)
                env["PLUGIN_DATA"] = str(plugin.data_root)
                cwd = _resolve_plugin_cwd(plugin, parsed.cwd)
                return MCPStdio(
                    name=private_alias,
                    transport="stdio",
                    command=[command],
                    args=args,
                    env=env,
                    cwd=str(cwd),
                )
            case _MCPStreamableHTTPServer():
                url = normalize_mcp_server_url(parsed.url)
                return MCPStreamableHttp(
                    name=private_alias,
                    transport="streamable-http",
                    url=url,
                    auth=MCPStaticAuth(headers=parsed.headers),
                )
            case _MCPSSEServer():
                return None

    def _load_skills(self, plugin: PluginDescriptor) -> dict[str, SkillInfo]:
        return self._load_skills_from_roots(plugin, (plugin.root / "skills",))

    def _load_skills_from_roots(
        self, plugin: PluginDescriptor, skill_roots: Sequence[Path]
    ) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {}
        for skills_dir in skill_roots:
            if not skills_dir.exists() and not skills_dir.is_symlink():
                continue
            try:
                resolved_skills = skills_dir.resolve(strict=True)
                if not resolved_skills.is_relative_to(plugin.root):
                    raise ValueError("skills must resolve inside the plugin root")
                if not resolved_skills.is_dir():
                    raise ValueError("skills must resolve to a directory")
                children = sorted(resolved_skills.iterdir(), key=lambda path: path.name)
            except (OSError, ValueError) as error:
                self._issues.append(
                    PluginConfigIssue(
                        file=skills_dir,
                        message=f"Failed to load skills: {error}",
                        code="plugin.path.outside_root",
                        fatal=False,
                        source_format=plugin.source_format,
                        component="skill",
                    )
                )
                continue

            for skill_dir in children:
                if not skill_dir.is_dir():
                    continue
                skill_path = skill_dir / "SKILL.md"
                try:
                    resolved_skill = skill_path.resolve(strict=True)
                    if not resolved_skill.is_relative_to(plugin.root):
                        raise ValueError("SKILL.md must resolve inside the plugin root")
                    if not resolved_skill.is_file():
                        continue
                    skill = self._parse_skill(resolved_skill, plugin)
                    expected_name = skill_dir.name
                    source_name = skill.name
                    if source_name != expected_name:
                        raise ValueError(
                            f"skill name {source_name!r} must match directory "
                            f"{expected_name!r}"
                        )
                except (OSError, ValueError, ValidationError, SkillParseError) as error:
                    if skill_path.exists() or skill_path.is_symlink():
                        self._issues.append(
                            PluginConfigIssue(
                                file=skill_path,
                                message=f"Failed to load: {error}",
                                code="plugin.skill.invalid",
                                fatal=False,
                                source_format=plugin.source_format,
                                component="skill",
                            )
                        )
                    continue
                alias = f"{plugin.namespace}:{source_name}"
                if alias in skills:
                    self._issues.append(
                        PluginConfigIssue(
                            file=resolved_skill,
                            message=f"Duplicate plugin skill alias {alias!r}",
                            code="plugin.skill.collision",
                            fatal=False,
                            source_format=plugin.source_format,
                            component="skill",
                        )
                    )
                    continue
                skills[alias] = skill.model_copy(
                    update={"name": alias, "source": SkillSource.PLUGIN}
                )
        return skills

    @staticmethod
    def _parse_skill(path: Path, plugin: PluginDescriptor) -> SkillInfo:
        try:
            content = read_safe(path, raise_on_error=True).text
        except OSError as error:
            raise SkillParseError(f"Cannot read file: {error}") from error
        frontmatter, body = parse_skill_markdown(content)
        metadata = SkillMetadata.model_validate(frontmatter)
        return SkillInfo.from_metadata(metadata, path, body.strip(), scope=plugin.scope)

    def _remove_same_scope_duplicates(
        self, candidates: Sequence[_PluginCandidate]
    ) -> list[_PluginCandidate]:
        by_name: dict[str, list[_PluginCandidate]] = defaultdict(list)
        for candidate in candidates:
            by_name[candidate.descriptor.name].append(candidate)
        selected: list[_PluginCandidate] = []
        for name, matches in by_name.items():
            if len(matches) == 1:
                selected.append(matches[0])
                continue
            for match in matches:
                self._issues.append(
                    PluginConfigIssue(
                        file=match.manifest_path,
                        message=f"Duplicate plugin name {name!r} at the same precedence",
                    )
                )
        return selected

    @staticmethod
    def _select_by_precedence(
        project: Sequence[_PluginCandidate], user: Sequence[_PluginCandidate]
    ) -> list[_PluginCandidate]:
        project_by_name = {
            candidate.descriptor.name: candidate for candidate in project
        }
        user_by_name = {candidate.descriptor.name: candidate for candidate in user}
        return [
            project_by_name[name] if name in project_by_name else user_by_name[name]
            for name in sorted(project_by_name.keys() | user_by_name.keys())
        ]

    def _remove_namespace_collisions(
        self, candidates: Sequence[_PluginCandidate]
    ) -> list[_PluginCandidate]:
        by_namespace: dict[str, list[_PluginCandidate]] = defaultdict(list)
        for candidate in candidates:
            by_namespace[candidate.descriptor.namespace].append(candidate)
        selected: list[_PluginCandidate] = []
        for namespace, matches in by_namespace.items():
            if len(matches) == 1:
                selected.append(matches[0])
                continue
            names = ", ".join(sorted(match.descriptor.name for match in matches))
            for match in matches:
                self._issues.append(
                    PluginConfigIssue(
                        file=match.manifest_path,
                        message=(
                            f"Plugin namespace {namespace!r} collides between {names}"
                        ),
                    )
                )
        return selected


NativePluginResolver = PluginResolver


def _compatibility_adapter(
    source_format: DetectedPluginFormat,
) -> PluginFormatAdapter | None:
    match source_format:
        case DetectedPluginFormat.CODEX:
            return CodexPluginAdapter()
        case DetectedPluginFormat.CLAUDE_CODE:
            return ClaudePluginAdapter()
        case DetectedPluginFormat.KIMI_CODE:
            return KimiPluginAdapter()
        case DetectedPluginFormat.OPENCODE:
            return OpenCodePluginAdapter()
        case _:
            return None


def plugin_skill_runtime_path(skill: SkillInfo) -> Path | None:
    value = skill.metadata.get(_RUNTIME_SKILL_PATH_METADATA)
    return Path(value) if value is not None else skill.skill_path


def plugin_skill_translation(skill: SkillInfo) -> Literal["synthetic_skill"] | None:
    value = skill.metadata.get(_SKILL_TRANSLATION_METADATA)
    return "synthetic_skill" if value == "synthetic_skill" else None


def _plugin_log_label(plugin: PluginDescriptor) -> str:
    version = plugin.version or "unversioned"
    return f"{plugin.name}@{version} ({plugin.source_format}, {plugin.scope})"


def _render_generated_skill(definition: AdaptedSkill) -> str:
    lines = [
        "---",
        f"name: {json.dumps(definition.source_name)}",
        f"description: {json.dumps(definition.description)}",
    ]
    if definition.allowed_tools:
        lines.append("allowed-tools: " + json.dumps(list(definition.allowed_tools)))
    lines.extend(["---", "", definition.prompt, ""])
    return "\n".join(lines)


def _typescript_identifier(value: str) -> str:
    return typescript_identifier(value)


def _private_server_alias(plugin: PluginDescriptor, source_id: str) -> str:
    # The plugin root is deliberately absent: a duplicate name is already an
    # error, so the name disambiguates on its own, and hashing the host path
    # would make the alias — and every execution name derived from it — differ
    # between two checkouts of the same plugin.
    identity = f"{plugin.name}\0{source_id}".encode()
    digest = hashlib.blake2s(identity, digest_size=8).hexdigest()
    source = _typescript_identifier(source_id or "server")[:80]
    return f"plugin_{digest}_{source}"


def _resolve_plugin_command(plugin_root: Path, command: str) -> str:
    if command.startswith("./"):
        resolved = (plugin_root / command[2:]).resolve(strict=False)
        if not resolved.is_relative_to(plugin_root):
            raise ValueError("stdio command must resolve inside the plugin root")
        return str(resolved)
    if "/" in command or "\\" in command or command in {".", ".."}:
        raise ValueError("stdio command must be a bare executable or start with './'")
    return command


def _expand_plugin_variables(value: str, plugin_root: Path, data_root: Path) -> str:
    replacements = {
        "${PLUGIN_ROOT}": str(plugin_root),
        "${PLUGIN_DATA}": str(data_root),
    }
    return _PLUGIN_PLACEHOLDER_PATTERN.sub(
        lambda match: replacements[match.group(0)], value
    )


def _validate_http_headers(value: dict[str, str]) -> dict[str, str]:
    normalized_names: set[str] = set()
    for name, header_value in value.items():
        if not _HTTP_HEADER_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid HTTP header name {name!r}")
        normalized_name = name.lower()
        if normalized_name in normalized_names:
            raise ValueError(f"duplicate HTTP header {name!r}")
        normalized_names.add(normalized_name)
        if any(
            character != "\t"
            and (
                ord(character) < _HTTP_HEADER_MIN_VISIBLE
                or ord(character) == _HTTP_HEADER_DELETE
                or ord(character) > _HTTP_HEADER_MAX_BYTE
            )
            for character in header_value
        ):
            raise ValueError(f"invalid HTTP header value for {name!r}")
    return value


def _resolve_plugin_cwd(plugin: PluginDescriptor, value: str | None) -> Path:
    if value is None:
        return plugin.root
    expanded = _expand_plugin_variables(value, plugin.root, plugin.data_root)
    if value.startswith("./"):
        expected_root = plugin.root
        candidate = plugin.root / value[2:]
    elif value == "${PLUGIN_ROOT}" or value.startswith("${PLUGIN_ROOT}/"):
        expected_root = plugin.root
        candidate = Path(expanded)
    elif value == "${PLUGIN_DATA}" or value.startswith("${PLUGIN_DATA}/"):
        expected_root = plugin.data_root
        candidate = Path(expanded)
    else:
        raise ValueError(
            "stdio cwd must start with './', '${PLUGIN_ROOT}', or '${PLUGIN_DATA}'"
        )
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(expected_root.resolve(strict=False)):
        raise ValueError("stdio cwd resolves outside its declared plugin root")
    return resolved


def _contained_component_file(plugin_root: Path, path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(plugin_root) or not resolved.is_file():
        raise ValueError(
            "component must resolve to a regular file inside the plugin root"
        )
    return resolved


def _contained_component_directory(plugin_root: Path, path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(plugin_root) or not resolved.is_dir():
        raise ValueError("component must resolve to a directory inside the plugin root")
    return resolved


def _validate_plugin_relative_path(value: str, component: str) -> None:
    if len(value) > _MAX_PLUGIN_COMPONENT_PATH_LENGTH or not value.startswith("./"):
        raise ValueError(
            f"{component} paths must start with './' and be at most 1024 characters"
        )
    relative = PurePosixPath(value[2:])
    if (
        not relative.parts
        or relative.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{component} paths must be contained POSIX-style paths")


def _validate_contained_library_tree(
    plugin_root: Path, requested_root: Path, resolved_root: Path
) -> None:
    if requested_root.is_symlink():
        raise ValueError("library paths cannot be symbolic links")
    for path in resolved_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("library folders cannot contain symbolic links")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(plugin_root):
            raise ValueError("library content resolves outside the plugin root")
        if not resolved.is_dir() and not resolved.is_file():
            raise ValueError("library folders can contain only files and directories")


def _validate_contained_knowledge_tree(plugin_root: Path, root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("knowledge folders cannot contain symbolic links")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(plugin_root):
            raise ValueError("knowledge content resolves outside the plugin root")
        if not resolved.is_dir() and not resolved.is_file():
            raise ValueError("knowledge folders can contain only files and directories")
