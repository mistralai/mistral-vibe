from __future__ import annotations

import asyncio
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
    Set as AbstractSet,
)
from dataclasses import dataclass, field, replace
from enum import StrEnum, auto
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import ValidationError

from vibe import __version__
from vibe._experimental_harness import (
    create_experimental_harness_host,
    runtime_startup_error,
)
from vibe.app_server._admin_config import (
    OrgConfigAttempt,
    OrgConfigReport,
    OrgConfigSource,
)
from vibe.app_server._agent_types import (
    AgentToolCatalogue,
    resolve_agent_types,
    workspace_agent_rejections,
    workspace_agent_types,
)
from vibe.app_server._cached_layers import (
    OIDC_TENANT_UNRESOLVED,
    apply_cached_layers,
    oidc_tenant_unresolved,
    org_config_key,
)
from vibe.app_server._client_provided_tools import (
    ClientProvidedTools,
    ClientToolDeclarationError,
    ClientTools,
)
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._ignored_tool_settings import ignored_tool_settings
from vibe.app_server._imports import build_local_imports_service
from vibe.app_server._launch_context import client_launch_context
from vibe.app_server._legacy_import import LegacySessionStore
from vibe.app_server._projection import (
    project_agent_summaries,
    project_config_view,
    project_skill_summaries,
)
from vibe.app_server._provided_tools import vibe_tool_groups
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server._session_config_cache import (
    BuiltSessionConfig,
    SessionConfigCache,
    SessionConfigKey,
)
from vibe.app_server._session_model import active_model_is_pinned
from vibe.app_server._session_spawn import spawn_models
from vibe.app_server._skills import discover_session_skills
from vibe.app_server.client import AppServerClient
from vibe.app_server.client_tools import ClientToolHandler
from vibe.app_server.connector_catalog import ConnectorCatalogService
from vibe.app_server.models import (
    AgentStatsSnapshot,
    ConfigIssue,
    ConnectorCounts,
    MCPState,
    ToolSummary,
)
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    RuntimeSnapshot,
    SessionMCPHttpServer,
    SessionMCPServer,
    SessionMCPStdioServer,
    SessionOptions,
    TransportKind,
)
from vibe.app_server.run_export import HeadlessUsageError, RunOutcome
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentProfile, BuiltinAgentName
from vibe.core.agents.registry import apply_profile_overrides
from vibe.core.config import (
    MCPHttp,
    MCPServer,
    MCPStaticAuth,
    MCPStdio,
    MCPStreamableHttp,
    SessionLoggingConfig,
    UtilityFeature,
    VibeConfigSchema,
    build_default_orchestrator,
    resolve_api_key,
)
from vibe.core.config.admin_config import AdminConfigApplyResult, AdminConfigOutcome
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.admin import enforced_keys
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.shell_timeouts import shell_max_timeout, shell_timeout_seconds
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.hooks.config import load_hooks_file, load_hooks_from_fs
from vibe.core.hooks.models import (
    HookConfig,
    HookConfigResult,
    HookProtocol,
    HookSource,
    HookType,
    RuntimeHookDefinition,
)
from vibe.core.llm.utility_completion import (
    ensure_utility_models_probed,
    select_utility_model,
)
from vibe.core.paths import VIBE_HOME
from vibe.core.prompts import UtilityPrompt
from vibe.core.session.session_permissions import start_restrict_session_log_permissions
from vibe.core.skills.models import SkillInfo
from vibe.core.system_prompt import (
    ProjectContextProvider,
    get_agents_md_section,
    get_commit_signature_section,
    git_bash_form,
)
from vibe.core.telemetry.types import LaunchContext
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.models import ToolPermission
from vibe.core.tools.permissions import PermissionStore
from vibe.core.utils import get_windows_bash_path, is_windows
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger
from vibe.utils import AgentEntrypoint
from vibe.utils.http import (
    configure_ssl_context,
    get_server_url_from_api_base,
    with_user_agent,
)
from vibe.utils.paths import is_dangerous_directory

_SHORT_SESSION_ID_LENGTH = 8
# A tool result past about 16,000 characters is saved to a file, and the model
# gets its first and last 4,000 or so with the file's path. The Harness Core
# counts about four characters a token. The gap between the two keeps a result
# just over the threshold from being saved for little gain.
_LARGE_OUTPUT_MAX_TOKENS = 4_000
_LARGE_OUTPUT_VISIBLE_TOKENS = 2_000
type _CommandEnvironmentMode = Literal["unix", "git_bash", "powershell"]


def _command_environment_mode() -> _CommandEnvironmentMode:
    if not is_windows():
        return "unix"
    if get_windows_bash_path() is not None:
        return "git_bash"
    return "powershell"


def _command_tool_name(mode: _CommandEnvironmentMode) -> str:
    """The Vibe tool whose settings configure the command environment's tool."""
    return "bash" if mode == "unix" else mode


def _bash_timeout(
    tool_name: str, overrides: Mapping[str, object] | None
) -> RustBashTimeoutSettings:
    """The command tool's default and maximum timeouts, in seconds, for Core.

    Both as the user set them in the configuration (or the agent profile), or
    Vibe's own when neither is set; the native tool's defaults do not apply.
    Each is read as the config schema reads it at load, exactly, so Core gets
    the number in the config. A default set alone within Vibe's own maximum
    runs under that maximum; other partial pairs are config errors at load.
    """
    from mistralai_vibe_local_harness.vibe import bash_timeout

    overrides = overrides or {}
    seconds = {
        key: shell_timeout_seconds(tool_name, key, overrides[key])
        for key in ("default_timeout", "max_timeout_seconds")
        if overrides.get(key) is not None
    }
    default = seconds.get("default_timeout")
    return bash_timeout(
        default,
        shell_max_timeout(default, seconds.get("max_timeout_seconds")),
        default_setting=f"tools.{tool_name}.default_timeout",
        max_setting=f"tools.{tool_name}.max_timeout_seconds",
    )


def _build_project_context_section(
    config: VibeConfigSchema, harness_files: HarnessFilesManager, cwd: Path
) -> str:
    """Replicate the legacy system prompt's project-context block.

    Includes the absolute working directory, git status (branch, main
    branch, porcelain status, recent commits), and any extra working
    directories — the same information ``build_system_prompt`` injects for
    the legacy backend.
    """
    from string import Template

    from vibe.core.prompts import UtilityPrompt

    is_dangerous, reason = is_dangerous_directory(cwd)
    if is_dangerous:
        template = UtilityPrompt.DANGEROUS_DIRECTORY.read()
        return Template(template).safe_substitute(
            reason=reason.lower(), abs_path=cwd.resolve()
        )

    context = ProjectContextProvider(
        config=config.project_context, root_path=cwd
    ).get_full_context()

    cwd_resolved = cwd.resolve()
    return _with_extra_roots(
        context,
        [
            root
            for root in harness_files.project_roots
            if root.resolve() != cwd_resolved
        ],
    )


def _prefetch_project_context(config: VibeConfigSchema, cwd: Path) -> None:
    # Git runs while the rest of the session is built; derivation waits on it.
    if not config.include_project_context or is_dangerous_directory(cwd)[0]:
        return
    ProjectContextProvider(
        config=config.project_context, root_path=cwd
    ).prefetch_git_status()


def _with_extra_roots(context: str, extra_roots: Sequence[Path]) -> str:
    if not extra_roots:
        return context
    dirs_lines = "\n".join(_extra_root_line(d) for d in extra_roots)
    return (
        f"{context}\n\nAdditional working directories (treated with the same "
        f"file-access permissions as the primary working directory):\n" + dirs_lines
    )


def _extra_root_line(root: Path) -> str:
    if git_bash_path := git_bash_form(root):
        return f" - {root} (Git Bash path: {git_bash_path})"
    return f" - {root}"


def _agent_profile_prompt(
    profile: AgentProfile,
) -> tuple[str | None, ConfigIssue | None]:
    """The prompt text an agent profile asks for, or why it cannot have it.

    Only a prompt id the profile itself declares is resolved as a file. The id
    the config carries otherwise is a GrowthBook variant the SDK owns, and
    reading a bundled Vibe prompt of the same name would quietly serve the
    legacy text instead.
    """
    from vibe.core.prompts import MissingPromptFileError, load_system_prompt

    prompt_id = profile.overrides.get("system_prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id:
        return None, None
    try:
        return load_system_prompt(prompt_id), None
    except (MissingPromptFileError, ValueError) as error:
        logger.warning("Agent %r: %s", profile.name, error)
        return None, ConfigIssue(
            file=str(profile.source_path or profile.name),
            message=f"Agent '{profile.name}': {error}",
        )


def _build_unified_system_instructions(
    config: VibeConfigSchema,
    harness_files: HarnessFilesManager,
    *,
    cwd: Path,
    base: str | None = None,
    extra: str | None = None,
    sandbox_workspace: SandboxWorkspaceContext | None = None,
    sandbox_roots: Sequence[Path] = (),
    project_instructions: str | None = None,
    background_processes: bool = False,
    command_environment: str = "unix",
) -> str:
    """The session's system instructions.

    With ``sandbox_workspace``, the project context and the project AGENTS.md
    docs come from the sandbox the tools run in rather than the host's disk;
    ``sandbox_roots`` are the session's open directories other than the cwd.
    ``project_instructions`` go with the project AGENTS.md docs, ahead of them.
    Only with ``background_processes``, in a ``command_environment`` the
    Harness Core advises the process tools for, does the shell guidance say to
    start long work in the background.
    """
    from mistralai_vibe_local_harness.vibe import (
        build_vibe_code_system_instructions,
        with_shell_guidance,
    )

    # The Vibe config layer resolves the GrowthBook system-prompt variant before
    # the experimental Runtime is composed. The SDK owns the corresponding text,
    # except when the active agent named a prompt of its own, which arrives as
    # ``base`` already read off disk. AGENTS.md docs are host-composed custom
    # instructions: the harness core never loads them, so parity with the legacy
    # prompt is restored here. The core appends its capability sections after
    # this text, keeping the docs above skills/plugins in the final prompt.
    instructions = with_shell_guidance(
        base or build_vibe_code_system_instructions(variant=config.system_prompt_id),
        background_processes=background_processes,
        command_environment=command_environment,
    )
    if extra:
        instructions = f"{instructions}\n\n{extra}"

    if config.include_commit_signature:
        instructions = f"{instructions}\n\n{get_commit_signature_section()}"

    if config.include_project_context:
        if sandbox_workspace is None:
            project_context = _build_project_context_section(config, harness_files, cwd)
            project_docs = harness_files.load_project_docs()
        else:
            project_context = _with_extra_roots(
                sandbox_workspace.project_context, sandbox_roots
            )
            project_docs = sandbox_workspace.project_docs
        instructions = f"{instructions}\n\n{project_context}"
        agents_md_section = get_agents_md_section(
            harness_files.load_user_doc(),
            project_docs,
            project_instructions=project_instructions,
        )
        if agents_md_section:
            instructions = f"{instructions}\n\n{agents_md_section}"
    return instructions


def _build_launch_context_from_services(
    services: SessionBackendServices,
) -> LaunchContext | None:
    """Read client metadata lazily after the app-server handshake."""
    try:
        client_info = services.client_info()
    except RuntimeError:
        return None
    return client_launch_context(client_info)


def session_spawner_for(services: SessionBackendServices) -> SessionSpawner | None:
    return services if services.client_capabilities().session_spawn else None


def session_peers_for(services: SessionBackendServices) -> SessionPeers | None:
    return services if services.client_capabilities().session_peers else None


def _auto_titles_enabled(config: VibeConfigSchema, entrypoint: str) -> bool:
    """Only interactive clients get background titles: terminal, desktop, and
    the VS Code extension's ACP surface (its sessions list renders titles).

    VIBE_TEST_DISABLE_AUTO_TITLE is the same kill switch the agent loop
    honors, for harnesses that run the real CLI against a mock model.
    """
    if os.environ.get("VIBE_TEST_DISABLE_AUTO_TITLE") == "1":
        return False
    return config.session_logging.generate_titles and entrypoint in {
        "cli",
        "desktop",
        "acp",
    }


def _named_provider(name: str) -> Callable[[VibeConfigSchema], ProviderConfig]:
    """Select a provider by name, so a key always matches its route's endpoint.

    Raising becomes an auth-required state in the credential service.
    """

    def select(config: VibeConfigSchema) -> ProviderConfig:
        provider = next((p for p in config.providers if p.name == name), None)
        if provider is None:
            raise ValueError(f"Provider '{name}' is no longer configured")
        return provider

    return select


def _output_room(window: int, threshold: int) -> int | None:
    # What a request at the compaction threshold leaves for output: servers that
    # check input plus max_tokens against the window refuse more. A window with
    # no room (a compaction model smaller than the agent's threshold) overflows
    # on input alone, so it bounds nothing.
    room = window - threshold
    return room if room > 0 else None


def _utility_provider_route(
    provider_cfg: ProviderConfig,
    *,
    session_provider_name: str,
    credentials: ProviderCredentialProvider,
    enabled: bool,
) -> LocalProviderRoute | None:
    """A utility completion's provider destination, or None to reuse the session's.

    Used by background title generation and the smart-approve classifier. The route
    is set only when the caller enables it and the selected provider differs from
    the session's active provider. Same-provider callers reuse the session adapter
    and its credentials, so the route stays None.
    """
    if not enabled or provider_cfg.name == session_provider_name:
        return None
    from mistralai_vibe_local_harness.vibe import LocalProviderRoute

    return LocalProviderRoute(
        provider=provider_cfg.name,
        backend=str(provider_cfg.backend),
        api_style=provider_cfg.api_style,
        base_url=provider_cfg.api_base,
        credentials=credentials,
        reasoning_field_name=provider_cfg.reasoning_field_name,
        emits_finish_reason=provider_cfg.emits_finish_reason,
        extra_headers=with_user_agent(provider_cfg.extra_headers, provider_cfg.backend),
        project_id=provider_cfg.project_id,
        region=provider_cfg.region,
    )


def _compaction_model_input_modalities(
    agent: ModelFacts, compaction: ModelFacts
) -> list[RustInputModality] | None:
    """What the compaction model reads, when it differs from the agent model."""
    from mistralai_vibe_local_harness.vibe import rust_input_modalities

    if list(compaction.input_modalities) == list(agent.input_modalities):
        return None
    return rust_input_modalities(compaction.input_modalities)


if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import (
        RustBashTimeoutSettings,
        RustInputModality,
        RustRuntimeBuiltinToolName,
    )
    from mistralai_vibe_local_harness.vibe import (
        HelperCrashCount,
        HookContext,
        LocalProviderRoute,
        ProviderCredentialProvider,
        SandboxAdapter,
    )
    from mistralai_vibe_local_harness.vibe.completion_profile import ModelFacts
    from mistralai_vibe_local_harness.vibe.plugins import DeclaredCompletionRoute
    from vibe.app_server._account import AccountGateway
    from vibe.app_server._agent_socket import AgentSocketMount
    from vibe.app_server._identity import IdentityGateway
    from vibe.app_server._mcp_auth import MCPAuthenticationService
    from vibe.app_server._org_config_service import OrgConfigKey
    from vibe.app_server._plugin_mcp import PluginMCPCatalog
    from vibe.app_server._plugins import SessionPlugins, UnifiedPluginProvider
    from vibe.app_server._process_services import ProcessServices
    from vibe.app_server._process_settings import ProcessSettings
    from vibe.app_server._sandbox_skills import SandboxSkills
    from vibe.app_server._sandbox_workspace import SandboxWorkspaceContext
    from vibe.app_server._session_backend_port import ResolvedMCPCatalog
    from vibe.app_server._session_peers import SessionPeers
    from vibe.app_server._session_spawn import SessionSpawner
    from vibe.app_server._subagent_permissions import SubagentPermissions
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedReadContext,
        UnifiedSessionContext,
    )
    from vibe.app_server._unified_import_writer import UnifiedSessionImportWriter
    from vibe.app_server.server import AppServer
    from vibe.core.config import ProviderConfig
    from vibe.core.tools.connectors.connector_registry import ConnectorRegistry


def merge_plugin_mcp_into_catalog(
    catalog: ResolvedMCPCatalog,
    plugin_mcp: PluginMCPCatalog,
    *,
    authentication: MCPAuthenticationService,
) -> ResolvedMCPCatalog:
    """Merge plugin-owned MCP servers the harness can run into a config catalog.

    A plugin source whose name a configured server already owns is skipped, so
    the harness runs the config entry. The plugin server's name never reaches
    the merged catalog, and ``plugin_owned_names`` reflects that.
    """
    from vibe.app_server._session_backend_port import (
        ResolvedMCPCatalog as _ResolvedMCPCatalog,
        ResolvedMCPServerConfig,
    )
    from vibe.core.config import MCPStdio

    configured_names = frozenset(server.name for server in catalog.servers)
    plugin_servers: list[ResolvedMCPServerConfig] = []
    for source in plugin_mcp.sources():
        if source.name in configured_names:
            continue
        server = source.entry.server
        reference = authentication.reference_for(server, owner="plugin")
        if isinstance(server, MCPStdio):
            argv = server.argv()
            plugin_servers.append(
                ResolvedMCPServerConfig(
                    name=source.name,
                    transport="stdio",
                    url=None,
                    command=argv[0] if argv else None,
                    args=tuple(argv[1:]),
                    cwd=(
                        Path(server.cwd).expanduser().resolve() if server.cwd else None
                    ),
                    env=server.env,
                    authorization=reference,
                    prompt=server.prompt,
                    startup_timeout_s=server.startup_timeout_sec,
                    tool_timeout_s=server.tool_timeout_sec,
                    sampling_enabled=server.sampling_enabled,
                    disabled=server.disabled,
                    disabled_tools=frozenset(server.disabled_tools),
                )
            )
        else:
            plugin_servers.append(
                ResolvedMCPServerConfig(
                    name=source.name,
                    transport=server.transport,
                    url=server.url,
                    command=None,
                    args=(),
                    cwd=None,
                    env={},
                    authorization=reference,
                    prompt=server.prompt,
                    startup_timeout_s=server.startup_timeout_sec,
                    tool_timeout_s=server.tool_timeout_sec,
                    sampling_enabled=server.sampling_enabled,
                    disabled=server.disabled,
                    disabled_tools=frozenset(server.disabled_tools),
                )
            )
    if not plugin_servers:
        return catalog
    all_servers = (*catalog.servers, *plugin_servers)
    payload = [
        {
            "name": server.name,
            "transport": server.transport,
            "url": server.url,
            "command": server.command,
            "args": list(server.args),
            "cwd": str(server.cwd) if server.cwd is not None else None,
            "env": dict(server.env),
            "authorization": {
                "server_name": server.authorization.server_name,
                "server_fingerprint": server.authorization.server_fingerprint,
                "kind": server.authorization.kind,
                "owner": server.authorization.owner,
            },
            "prompt": server.prompt,
            "startup_timeout_s": server.startup_timeout_s,
            "tool_timeout_s": server.tool_timeout_s,
            "sampling_enabled": server.sampling_enabled,
            "disabled": server.disabled,
            "disabled_tools": sorted(server.disabled_tools),
        }
        for server in all_servers
    ]
    revision = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return _ResolvedMCPCatalog(revision=revision, servers=all_servers)


def plugin_owned_names(catalog: ResolvedMCPCatalog) -> frozenset[str]:
    """Names in a merged catalog that a plugin owns, not a config entry.

    Derived from the catalog the harness was actually configured with, so a
    name a configured server won is absent here even though a plugin source
    declared it. This is the set the authorization adapter needs to route
    ``owner`` on references the harness sends back.
    """
    return frozenset(
        server.name
        for server in catalog.servers
        if server.authorization.owner == "plugin"
    )


@dataclass(frozen=True, slots=True)
class NewSessionIntent:
    pass


@dataclass(frozen=True, slots=True)
class ContinueSessionIntent:
    pass


@dataclass(frozen=True, slots=True)
class ResumeSessionIntent:
    session_id: str

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("A session ID is required to resume a session")


type LocalSessionIntent = NewSessionIntent | ContinueSessionIntent | ResumeSessionIntent


@dataclass(frozen=True, slots=True)
class ClientDescriptor:
    info: ClientInfo
    capabilities: ClientCapabilities = field(default_factory=ClientCapabilities)


def _default_client() -> ClientDescriptor:
    return ClientDescriptor(info=ClientInfo(name="vibe_client", version=__version__))


@dataclass(frozen=True, slots=True)
class LocalHarnessOptions:
    client: ClientDescriptor = field(default_factory=_default_client)
    session_options: SessionOptions = field(
        default_factory=lambda: SessionOptions(cwd=str(Path.cwd().resolve()))
    )
    session: LocalSessionIntent = field(default_factory=NewSessionIntent)
    client_tool_handler: ClientToolHandler | None = None

    # Runs the workspace tools in this sandbox instead of on the host, and
    # reads the workspace context through it. Requires the Unified Harness.
    sandbox: SandboxAdapter | None = field(default=None, kw_only=True)
    # Tools the embedding host runs itself, offered to every session next to
    # Vibe's own. Internal: nothing on the wire declares them. Requires the
    # Unified Harness.
    client_tools: ClientTools | None = field(default=None, kw_only=True)
    # The user config to read in place of the user config file, as the parsed
    # TOML document. Saves to it stay in memory. Internal: an embedding host
    # serving many runs gives each its own.
    user_config: Mapping[str, Any] | None = field(default=None, kw_only=True)
    # Project instructions the embedding host gives every session, shown to
    # the model ahead of the project's AGENTS.md docs. Requires the Unified
    # Harness.
    project_instructions: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class _SessionConfig:
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    harness_files: HarnessFilesManager
    org_config: OrgConfigAttempt
    org_config_key: OrgConfigKey | None


# The harness always binds a session to a concrete on-disk store: there is no
# in-memory backend, and a ``None`` root silently falls back to the configured save
# dir. One root per process, because ``configure_storage`` refuses to move the root
# while a session is bound to it. Every app-server on the process holds a lease, so
# the last one out removes the tree: a stop is not the end of the process, and ACP
# keeps opening sessions on it. A later session gets a fresh root, which
# ``configure_storage`` accepts because nothing is bound by then.
class ThrowawayStorageRoot:
    PREFIX = "vibe-session-logging-disabled-"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._leases: set[ThrowawayStorageLease] = set()

    @property
    def current(self) -> Path | None:
        with self._lock:
            return self._path

    def lease(self) -> ThrowawayStorageLease:
        lease = ThrowawayStorageLease(self)
        with self._lock:
            self._leases.add(lease)
        return lease

    def path(self) -> Path:
        with self._lock:
            if self._path is None:
                try:
                    self._path = Path(tempfile.mkdtemp(prefix=self.PREFIX))
                except OSError as exc:
                    raise RuntimeError(
                        "session_logging.enabled is false and no temporary "
                        "directory is available to hold the session store"
                    ) from exc
                logger.debug("Throwaway session store created at %s", self._path)
            return self._path

    def release(self, lease: ThrowawayStorageLease) -> None:
        with self._lock:
            # Dropping a lease rather than counting down, because the same lease
            # is released twice: `session/stop` closes the root, and a disconnect
            # closes it again, the first never having marked the app server shut
            # down. Idempotent here by construction, not by a flag the callers
            # race on from worker threads.
            self._leases.discard(lease)
            if self._leases:
                return
            path, self._path = self._path, None
        self._remove(path)

    def discard(self) -> None:
        with self._lock:
            self._leases.clear()
            path, self._path = self._path, None
        self._remove(path)

    def _remove(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            shutil.rmtree(path, onexc=_reopen_and_retry)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(
                "Failed to remove the throwaway session store at %s", path, exc_info=exc
            )


class ThrowawayStorageLease:
    def __init__(self, root: ThrowawayStorageRoot) -> None:
        self._root = root

    def release(self) -> None:
        self._root.release(self)


def _reopen_and_retry(
    func: Callable[[str], None], path: str, exc: BaseException
) -> None:
    # The Harness checks plugin packages out as read-only trees under the same
    # storage root the sessions live in, and nothing can be unlinked from one until
    # its directory is writable again.
    if not isinstance(exc, PermissionError):
        raise exc
    parent = Path(path).parent
    parent.chmod(parent.stat().st_mode | stat.S_IRWXU)
    func(path)


@dataclass(frozen=True, slots=True)
class HarnessServer:
    _server: AppServer
    _transport: JsonRpcTransport
    _reconnectable: bool = False

    async def serve(self) -> None:
        await self._server.serve_connection(
            self._transport, close_on_disconnect=not self._reconnectable
        )

    def connect_client(self) -> AppServerClient:
        if not self._reconnectable:
            raise RuntimeError("This app-server transport cannot reconnect")
        client_transport, server_transport = memory_transport_pair()
        return AppServerClient(
            client_transport,
            run_peer=lambda: self._server.serve_connection(
                server_transport, close_on_disconnect=False
            ),
        )


class HarnessProcess:
    def __init__(  # noqa: PLR0913 - one collaborator per concern, all keyword-only
        self,
        harness_files: HarnessFilesManager | None = None,
        *,
        services: ProcessServices,
        additional_builtin_plugin_roots: Sequence[Path] = (),
        shared: bool = False,
        sandbox: SandboxAdapter | None = None,
        client_tools: ClientTools | None = None,
        user_config: Mapping[str, Any] | None = None,
        project_instructions: str | None = None,
        agent_socket: AgentSocketMount | None = None,
        credentials: Mapping[str, str] | None = None,
        process_settings: ProcessSettings | None = None,
    ) -> None:
        from vibe.app_server._mcp_auth import MCPAuthenticationService
        from vibe.app_server._plugins import packaged_builtin_plugin_roots
        from vibe.app_server.mcp_catalog import MCPCatalogService
        from vibe.app_server.plugin_catalog import PluginCatalogService

        # Shared with every other channel of the OS process, which owns them.
        self.services = services
        # The agent server's sandbox, tools and instructions, when the
        # handshake succeeded; otherwise every session open reports its failure.
        self._agent_socket = agent_socket
        # A run's own API keys, by environment variable name: they resolve
        # before this process's, for its model calls only.
        self._credentials = credentials
        # What this process applies to every run, which a session's config
        # must agree with.
        self._process_settings = process_settings
        if agent_socket is not None and agent_socket.agent_socket is not None:
            sandbox = agent_socket.agent_socket.sandbox
            client_tools = agent_socket.agent_socket.client_tools()
            project_instructions = agent_socket.agent_socket.instructions
        if (
            sandbox is not None
            and harness_files is not None
            and "project" in harness_files.sources
        ):
            # The project lives in the sandbox; its config, skills and hooks
            # would be read from the host's disk at the same path.
            raise HeadlessUsageError("A sandboxed session cannot load project files")
        self.imports = build_local_imports_service(self.build_import_writer)
        self.harness_files = harness_files or HarnessFilesManager(
            sources=("user",) if sandbox is not None else ("user", "project")
        )
        if user_config is not None:
            self.harness_files = replace(self.harness_files, user_config=user_config)
        self._sandbox = sandbox
        # The tool helper's crashes in a row in the sandbox, counted across
        # this process's sessions, as they share the sandbox. Made with the
        # first unified session.
        self._helper_crashes: HelperCrashCount | None = None
        # The skills copied into the sandbox, shared by this process's sessions.
        self._sandbox_skills: SandboxSkills | None = None
        if sandbox is not None:
            from vibe.app_server._sandbox_skills import SandboxSkills

            self._sandbox_skills = SandboxSkills(sandbox)
        # First: tools Vibe cannot offer fail the process before any session.
        self._client_tools = ClientProvidedTools(client_tools)
        self._project_instructions = project_instructions
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._shared = shared
        self._experimental_harness_host: object | None = None
        self._throwaway_storage_root = ThrowawayStorageRoot()
        self._session_configs = SessionConfigCache()
        self._builtin_plugin_roots = [
            *packaged_builtin_plugin_roots(),
            *additional_builtin_plugin_roots,
        ]
        candidate = create_experimental_harness_host()
        # The Host-global builtin hook registry: the lazy AGENTS.md
        # injection handler must not land in any session's *foreign*
        # handler map, or it surfaces public hook-run notices. The
        # matching binding is merged into every session's compiled
        # hooks at context build (see merge_agents_md_hook).
        from vibe.app_server._agents_md_hooks import agents_md_hook_handlers

        try:
            cast(Any, candidate).configure_hook_handlers(
                agents_md_hook_handlers(self.harness_files)
            )
        except Exception as exc:
            # An older pinned harness without the builtin-hook registry
            # (or one whose import surface the version cannot satisfy)
            # is an incompatible Runtime: startup must fail with the
            # actionable error, not hand out an unconfigured Host. The
            # candidate needs no teardown — create_harness_host is a
            # pure construction with no native side effects, and every
            # caller aborts the process on this error.
            raise runtime_startup_error(
                f"the runtime host rejected its configuration: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        self._experimental_harness_host = candidate
        self.host_handler = HostRequestHandler(
            self.harness_files,
            org_config=services.org_config,
            image_fallback=self._experimental_harness_host is not None,
            builtin_plugin_roots=self._builtin_plugin_roots,
        )
        self.mcp_authentication = MCPAuthenticationService()
        self.mcp_catalog = MCPCatalogService(
            self.mcp_authentication,
            sessionless_catalog_factory=self.build_sessionless_mcp_catalog,
        )
        self.connector_catalog = ConnectorCatalogService(
            implicit_source_enabled=self._experimental_harness_host is not None,
            sessionless_catalog_factory=self.build_sessionless_mcp_catalog,
        )
        self.plugin_catalog = PluginCatalogService()
        self._connector_warm_up: asyncio.Task[None] | None = None

    def start_connector_warm_up(self) -> None:
        """Fetch the connector catalog now, before any session needs it.

        A session's first turn waits for the catalog when nothing is cached,
        and every session joins this fetch rather than starting its own. Its
        failure is not memoized, so a session's own load retries at once and
        reports it.

        Only the stdio servers start it; the process is left to end with them.
        """
        if self._connector_warm_up is None and not self._closed:
            self._connector_warm_up = asyncio.create_task(
                self._warm_connector_catalog(), name="vibe-connector-warm-up"
            )

    async def _warm_connector_catalog(self) -> None:
        try:
            orchestrator = await self.build_sessionless_mcp_catalog()
            await self.connector_catalog.resolve_catalog(
                orchestrator, remember_failure=False
            )
        except Exception as exc:
            logger.info("Connector catalog warm-up failed: %s", exc)

    def create_session_backend_host(
        self, services: SessionBackendServices
    ) -> SessionBackendHost:
        host = self._experimental_harness_host
        from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

        if self._agent_socket is not None:
            self._agent_socket.subscribe(services.notify)

        def build_session_context(
            options: SessionOptions,
            *,
            require_api_key: bool = True,
            entrypoint: AgentEntrypoint = "cli",
            org_config: OrgConfigSource,
        ) -> Awaitable[UnifiedSessionContext]:
            return self.build_unified_session_context(
                options,
                require_api_key=require_api_key,
                entrypoint=entrypoint,
                org_config=org_config,
                session_spawner=session_spawner_for(services),
                session_peers=session_peers_for(services),
            )

        return adapt_harness_host(
            host,
            build_session_context,
            services,
            pin_session_options=None
            if self._agent_socket is None
            else self._agent_socket.pin_session_options,
            build_read_context=self.build_unified_read_context,
            launch_context_getter=lambda: _build_launch_context_from_services(services),
            # Neither the TUI nor ``vibe -p`` reaches ``HarnessProcess.close``
            # on the way out; both stop at the host's shutdown. A store hung
            # off ``close`` alone would survive every interactive run. A
            # shared process outlives each app server: only ``close`` may shut
            # its Host down and discard the store the Host still writes into.
            release_storage=(
                None if self._shared else self._throwaway_storage_root.lease().release
            ),
            owns_host=not self._shared,
        )

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if (warm_up := self._connector_warm_up) is not None:
            warm_up.cancel()
            # Not ``suppress``: a cancellation of ``close`` itself must still land.
            await asyncio.wait([warm_up])
        errors: list[BaseException] = []
        try:
            await self.imports.close()
        except BaseException as exc:
            errors.append(exc)
        if self._experimental_harness_host is not None:
            try:
                await cast(Any, self._experimental_harness_host).shutdown()
            except BaseException as exc:
                errors.append(exc)
        await asyncio.to_thread(self._throwaway_storage_root.discard)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close the harness process", errors)

    def _unified_storage_root(self, session_logging: SessionLoggingConfig) -> str:
        if session_logging.enabled:
            return session_logging.save_dir
        return str(self._throwaway_storage_root.path())

    async def build_sessionless_mcp_catalog(
        self,
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        session_config = await self._build_session_config(SessionOptions())
        return session_config.config_orchestrator

    async def build_import_writer(self) -> UnifiedSessionImportWriter:
        from vibe.app_server._unified_import_writer import UnifiedSessionImportWriter

        session_config = await self._build_session_config(SessionOptions())
        return UnifiedSessionImportWriter(
            session_config.config_orchestrator.config.session_logging
        )

    async def build_unified_read_context(
        self, options: SessionOptions
    ) -> UnifiedReadContext:
        """Deliberately not ``build_unified_session_context``: a read resolves no
        model, tool, plugin, MCP server or connector.
        """
        from vibe.app_server._unified_harness_backend_adapter import UnifiedReadContext

        session_config = await self._build_session_config(options)
        config_orchestrator = session_config.config_orchestrator
        legacy_store = LegacySessionStore(config_orchestrator.config)

        def build_agents() -> AgentManager:
            return AgentManager(
                config_orchestrator,
                options.agent,
                harness_files=session_config.harness_files,
            )

        return UnifiedReadContext(
            storage_root=self._unified_storage_root(
                config_orchestrator.config.session_logging
            ),
            config_orchestrator=config_orchestrator,
            harness_files=session_config.harness_files,
            legacy_source_loader=legacy_store.loader(),
            legacy_source_resolver=legacy_store.resolver(),
            build_agents=build_agents,
        )

    async def build_unified_session_context(
        self,
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: AgentEntrypoint = "cli",
        org_config: OrgConfigSource = OrgConfigSource.CACHED,
        session_spawner: SessionSpawner | None = None,
        session_peers: SessionPeers | None = None,
    ) -> UnifiedSessionContext:
        """Build a session context; no build fails because of the org config.

        ``org_config`` is ``ENSURED`` for a session start: it waits for a first
        copy when the process holds none. Every other build uses the copy the
        process holds. When the session cannot be built or run under it, the
        build falls back to local config. A failure with no
        org config applied is the local config's, and propagates. Nothing is
        removed from the cache: the failure can be this project's or this
        user's local config, not the org config's. The context carries the
        report for the session to publish.
        """
        source = org_config
        error: Exception | None = None
        first: OrgConfigAttempt | None = None
        while True:
            session_config = await self._build_session_config(
                options, org_config=source, check_process_settings=False
            )
            applied = session_config.org_config
            first = first or applied
            try:
                if self._process_settings is not None:
                    self._process_settings.check(
                        session_config.config_orchestrator.config
                    )
                context = await self._build_unified_session_context_once(
                    options,
                    session_config,
                    require_api_key=require_api_key,
                    entrypoint=entrypoint,
                    session_spawner=session_spawner,
                    session_peers=session_peers,
                )
                await _accept_org_config(context)
                break
            except Exception as exc:
                # A context the Core check rejected is dropped without being
                # closed: nothing in it is open yet. Its experiment client
                # connects on the first evaluation, after the session opens,
                # and the MCP auth service holds its bindings by weak reference.
                retry = applied.next_source()
                if retry is None:
                    raise
                error = error or exc
                source = retry
        outcome = fetch_outcome = first.fetch_outcome
        if error is not None:
            logger.warning("Org config rejected by the session", exc_info=error)
            outcome = AdminConfigApplyResult(
                AdminConfigOutcome.APPLY_FAILED, error=str(error)
            )
        elif (cache_failure := applied.cache_failure) is not None:
            outcome = cache_failure
        context.org_config_report.report = OrgConfigReport(
            outcome,
            excluded_agent=context.agents.excluded_agent,
            fetch_outcome=fetch_outcome,
        )
        # What the follower compares a refresh against: a copy this build
        # could not run under is not news to apply again.
        context.org_config_report.loaded = first.considered or ""
        return context

    async def _build_unified_session_context_once(  # noqa: PLR0914, PLR0915 - composition root
        self,
        options: SessionOptions,
        session_config: _SessionConfig,
        *,
        require_api_key: bool,
        entrypoint: AgentEntrypoint,
        session_spawner: SessionSpawner | None,
        session_peers: SessionPeers | None,
    ) -> UnifiedSessionContext:
        from mistralai_vibe_local_harness import HarnessSession
        from mistralai_vibe_local_harness.protocol import (
            RustAutomaticCompactionPolicy,
            RustContextSettings,
            RustDisabledOutputLimitRecovery,
            RustDisabledRuntimeToolFeature,
            RustEnabledAgentTools,
            RustEnabledModelWait,
            RustEnabledRuntimeToolFeature,
            RustFilesystemLargeOutputPolicy,
            RustGitBashCommandEnvironment,
            RustHarnessCapabilitySet,
            RustHarnessConfig,
            RustHarnessSettings,
            RustPowerShellCommandEnvironment,
            RustProgrammaticToolSettings,
            RustProvidedToolDefinition,
            RustRetryOutputLimitRecovery,
            RustToolGroupDefinition,
            RustToolSettings,
            RustTurnSettings,
            RustUnixCommandEnvironment,
        )
        from mistralai_vibe_local_harness.vibe import (
            HelperCrashCount,
            LocalModelRoute,
            LocalRuntimeAdapterConfig,
            SessionWorkspace,
            compile_foreign_hooks,
            rust_model_facts,
            sandbox_path,
            tool_catalog_for_config,
        )
        from vibe.app_server._agents_md_hooks import merge_agents_md_hook
        from vibe.app_server._completion_facts import (
            ModelFactsError,
            SavedCatalogue,
            compile_session_model_facts,
        )
        from vibe.app_server._plugins import (
            core_plugins,
            plugin_agent_names,
            plugin_issues,
            requested_plugin_definitions,
        )

        # Import the provider service after loading the bundled Harness package.
        from vibe.app_server._provider_credentials import ProviderCredentialService
        from vibe.app_server._subagent_permissions import SubagentPermissions
        from vibe.app_server._subagent_runs import SubagentSteps
        from vibe.app_server._unified_harness_backend_adapter import (
            CorrelationIdHolder,
            MalformedToolCallsQueue,
            OrgConfigKeySlot,
            RequestSentQueue,
            UnifiedConfigCandidate,
            UnifiedRuntimeDerivation,
            UnifiedSessionContext,
            UnifiedSessionSettings,
        )
        from vibe.app_server._unified_permissions import (
            ProvidedToolNames,
            UnifiedPermissionResolver,
        )
        from vibe.questions import UserQuestionRequest, UserQuestionResult

        config_orchestrator = session_config.config_orchestrator
        harness_files = session_config.harness_files
        config = config_orchestrator.config
        sandbox = self._sandbox
        if self._helper_crashes is None:
            self._helper_crashes = HelperCrashCount()
        helper_crashes = self._helper_crashes
        cwd = self._session_cwd(options)
        if sandbox is None:
            _prefetch_project_context(config, cwd)
        # The harness reads a non-empty root set as the complete one, so the cwd
        # has to stay in it: ``--add-dir`` widens the workspace, never replaces it.
        if sandbox is None:
            workspace = SessionWorkspace(
                cwd=cwd,
                roots=(
                    cwd,
                    *(Path(root).expanduser() for root in options.workspace_roots),
                ),
            )
        else:
            # Sandbox paths: normalized, never resolved against the host.
            workspace = SessionWorkspace(
                cwd=cwd,
                roots=(
                    cwd,
                    *(sandbox_path(root, cwd=cwd) for root in options.workspace_roots),
                ),
                path_space="sandbox",
            )
        # Built before the plugins, which bind the workspace's own agent types
        # through the same projection as a plugin's and therefore need a manager
        # to read them from. Both the credential service and the agent manager
        # read the orchestrator lazily, so they stay correct across every config
        # mutation and must outlive any single derivation.
        agents = AgentManager(
            config_orchestrator, options.agent, harness_files=harness_files
        )
        # Derivation reads the probe's answer synchronously, so it must finish
        # first. Only features on at open are probed; one turned on later falls
        # back until the next session open.
        probe_features = tuple(
            feature
            for feature, enabled in (
                (
                    UtilityFeature.SMART_APPROVE,
                    agents.active_profile.name == BuiltinAgentName.SMART_APPROVE,
                ),
            )
            if enabled
        )

        def completion_route(
            overrides: Mapping[str, Any],
        ) -> DeclaredCompletionRoute | None:
            # Resolved as the session resolves its own model, from the profile's
            # layer over the session's config; only the alias lookup differs, as
            # a model the allowlist excludes is refused rather than replaced.
            from mistralai_vibe_local_harness.vibe.plugins import (
                DeclaredCompletionRoute,
            )

            alias = overrides.get("active_model")
            if alias is None:
                return None
            profile_config = config_orchestrator.copy()
            apply_profile_overrides(profile_config, dict(overrides))
            config = profile_config.config
            model = config.models.get(alias) if isinstance(alias, str) else None
            if model is None:
                raise ValueError(
                    f"active_model: {alias!r} is not an alias defined in [[models]]"
                )
            if alias not in config.available_models():
                raise ValueError(
                    f"active_model: {alias!r} ({model.name}) is excluded by "
                    "allowed_models"
                )
            session = config_orchestrator.config
            # The destination comes from the session's own providers: the session's
            # key for a provider must not follow an endpoint the profile redefined.
            provider_cfg = session.get_provider_for_model(model)
            # Compiled as the session compiles its own agent route, so the child
            # gets the same fitted thinking level, output cap and Core facts.
            try:
                facts = compile_session_model_facts(provider_cfg, model, model)
            except ModelFactsError as error:
                raise ValueError(
                    f"active_model: {alias!r} ({model.name}): {error}"
                ) from error
            return DeclaredCompletionRoute(
                model=LocalModelRoute(
                    model=model.name,
                    temperature=model.temperature,
                    thinking=facts.agent_thinking,
                    top_p=model.top_p,
                    max_output_tokens=facts.agent.max_output_tokens,
                    output_room=_output_room(
                        facts.agent.context_window, facts.agent.compaction_threshold
                    ),
                    reasoning=facts.agent_reasoning,
                ),
                provider=_utility_provider_route(
                    provider_cfg,
                    session_provider_name=session.get_provider_for_model(
                        session.get_active_model()
                    ).name,
                    credentials=credentials_for(provider_cfg.name),
                    enabled=True,
                ),
                model_facts=rust_model_facts(facts.agent),
            )

        # A sandbox is a POSIX environment whatever the host runs.
        command_environment_mode: _CommandEnvironmentMode = (
            "unix" if sandbox is not None else _command_environment_mode()
        )
        # The store is the session's memory of what the user approved
        permissions = PermissionStore()

        def session_tools(config_getter: Callable[[], VibeConfigSchema]) -> ToolManager:
            return ToolManager(
                config_getter,
                defer_mcp=True,
                cwd=workspace.cwd,
                harness_files=harness_files,
                permission_getter=permissions.get_tool_permission,
            )

        # The MCP and connector projections write the routes they published into
        # this map; the resolver reads it to find the name they were configured
        # under. Both need the same instance, so the context carries it.
        provided_names = ProvidedToolNames()
        permission_resolver = UnifiedPermissionResolver(
            session_tools(lambda: config_orchestrator.config),
            permissions,
            config_orchestrator,
            provided_names,
            path_space=workspace.path_space,
            command_environment=command_environment_mode,
        )
        # Built before the plugins, which hand each declared subagent profile the
        # resolver its calls go through.
        subagent_permissions = SubagentPermissions(
            permission_resolver, config_orchestrator, session_tools
        )
        (plugins, plugin_provider, plugin_mcp), _ = await asyncio.gather(
            self._build_plugins(
                session_config,
                workspace.cwd,
                agents=agents,
                subagent_permissions=subagent_permissions,
                completion_route=completion_route,
            ),
            ensure_utility_models_probed(config, features=probe_features),
        )
        match command_environment_mode:
            case "unix":
                command_environment = RustUnixCommandEnvironment()
            case "git_bash":
                command_environment = RustGitBashCommandEnvironment()
            case "powershell":
                command_environment = RustPowerShellCommandEnvironment()

        # Constructed once per session rather than per derivation: it holds the
        # set of revisions a provider has refused, and a service rebuilt on
        # every derivation would forget them and re-send a rejected key.
        credentials = ProviderCredentialService(
            config_orchestrator, credentials=self._credentials
        )

        # A route's endpoint is fixed at derivation, so its key is pinned to the
        # same provider. Per session, like ``credentials``, so rejection memory
        # survives derivations.
        utility_credentials: dict[str, ProviderCredentialService] = {}

        def credentials_for(provider_name: str) -> ProviderCredentialService:
            if provider_name not in utility_credentials:
                utility_credentials[provider_name] = ProviderCredentialService(
                    config_orchestrator,
                    select_provider=_named_provider(provider_name),
                    credentials=self._credentials,
                )
            return utility_credentials[provider_name]

        # One holder per session, shared by every derivation's adapter config and
        # the context: the Mistral adapter writes the provider correlation id into
        # it, and telemetry forwarding reads it.
        correlation = CorrelationIdHolder()

        # Same lifetime and sharing as ``correlation``: the completion adapter
        # appends each request's shape here, the adapter drains it to telemetry.
        request_sent = RequestSentQueue()
        malformed_tool_calls = MalformedToolCallsQueue()
        # The catalogue answer a reopened session saved; the adapter sets it from
        # the session's pin before the derivation that restores it.
        saved_catalogue = SavedCatalogue()
        # The root and its subagents share the sink: it counts each subagent's
        # agent model calls for ``session/subagents/read``.
        subagent_steps = SubagentSteps()

        if require_api_key:
            config_orchestrator.config.require_active_provider_api_key()
        # Read once per session, like the host's own git snapshot: the context
        # describes the workspace as the session found it.
        sandbox_workspace = None
        sandbox_skills = None
        sandbox_roots: tuple[Path, ...] = ()
        if sandbox is not None:
            from vibe.app_server._sandbox_workspace import (
                read_sandbox_workspace_context,
            )
            from vibe.core.skills.manager import SkillManager

            try:
                sandbox_roots = tuple(
                    dict.fromkeys(root for root in workspace.roots if root != cwd)
                )
                if config.include_project_context:
                    sandbox_workspace = await read_sandbox_workspace_context(
                        sandbox,
                        config.project_context,
                        cwd=cwd,
                        roots=(cwd, *sandbox_roots),
                    )
                # The model reads skills with tools that run in the sandbox: the
                # host's skills are copied there, and the project's are read there.
                assert self._sandbox_skills is not None
                root_skills = await asyncio.to_thread(
                    lambda: (
                        SkillManager(
                            lambda: config_orchestrator.config,
                            harness_files=harness_files,
                        ).available_skills
                    )
                )
                sandbox_skills = await self._sandbox_skills.prepare_session(
                    [
                        *root_skills.values(),
                        *plugins.materialized.resolution.skills.values(),
                    ],
                    roots=(cwd, *sandbox_roots),
                )
            finally:
                # The agent server's abort, however the sandbox reads reported
                # it: some, such as the git context, swallow their failures.
                if self._agent_socket is not None:
                    self._agent_socket.check()
        legacy_store = LegacySessionStore(config_orchestrator.config)

        # Discover the user's hooks once. The raw parse -- including any parse/duplicate
        # diagnostics on result.issues -- is settings-independent, so load it before the
        # derivation closure and surface those diagnostics as session notices, matching
        # legacy (which projects hook_config_issues) and the design's failure table.
        hook_result = await asyncio.to_thread(
            load_hooks_from_fs, harness_files=session_config.harness_files
        )
        # Plugin hooks bind with the user's own when the session starts. A
        # ``plugin/reload`` swaps the plugin set a derivation sees, but cannot rebind
        # the session's hooks, so it tells the user they apply to new sessions.
        plugin_hooks, plugin_hook_issues = _runnable_plugin_hooks(plugins)
        mcp_catalog = await self.mcp_catalog.resolve_catalog(config_orchestrator)
        if config_orchestrator.config.enable_mcp:
            mcp_catalog = merge_plugin_mcp_into_catalog(
                mcp_catalog, plugin_mcp, authentication=self.mcp_authentication
            )
            try:
                self._client_tools.check_mcp_server_names(
                    server.name for server in mcp_catalog.servers
                )
            except ClientToolDeclarationError as e:
                from vibe.app_server._agent_socket import session_open_error

                raise session_open_error(RunOutcome.USAGE_ERROR, str(e)) from e
        # Connector discovery is deferred to a background task so it never blocks
        # session/start. The session opens with an empty connector catalog and the
        # adapter resolves it after start, reconfiguring connectors in-place when
        # the catalog arrives. The 10-minute on-disk cache (connector_bootstrap_cache.json)
        # makes the resolve instant on warm hits; only a cold cache pays the network
        # round-trip, and that now overlaps with the user seeing the session/picker.
        connector_catalog = None
        connector_selection = self.connector_catalog.resolve_selection(
            config_orchestrator, connector_catalog
        )
        connector_provider = config.get_mistral_provider()
        connector_api_key = ""
        connector_base_url = "https://api.mistral.ai"
        if connector_provider is not None:
            connector_api_key = (
                resolve_api_key(connector_provider.api_key_env_var or "MISTRAL_API_KEY")
                or ""
            )
            connector_base_url = (
                get_server_url_from_api_base(connector_provider.api_base)
                or connector_base_url
            )

        def derive_config(  # noqa: PLR0914 - one cohesive config derivation
            config: VibeConfigSchema,
            settings: UnifiedSessionSettings,
            candidate: UnifiedConfigCandidate | None = None,
        ) -> UnifiedRuntimeDerivation:
            # A candidate not installed yet derives from itself throughout.
            orchestrator = (
                config_orchestrator if candidate is None else candidate.orchestrator
            )
            agent_view = agents if candidate is None else candidate.agents
            active_profile = agent_view.active_profile
            active_model = config.get_active_model()
            provider = config.get_provider_for_model(active_model)
            compaction_model = config.get_compaction_model()
            title_model_config = (
                config.get_utility_model(UtilityFeature.TITLE)
                or config.get_active_model()
            )
            title_provider_cfg = config.get_provider_for_model(title_model_config)
            classifier_model_config, classifier_provider_cfg = select_utility_model(
                config, feature=UtilityFeature.SMART_APPROVE
            )
            # Only settings the model accepts: Mistral rejects greedy sampling
            # (temperature 0.0) unless top_p is 1, and some models reject "off".
            # Without "off", the title keeps the model's own level.
            title_model = LocalModelRoute(
                model=title_model_config.name,
                temperature=title_model_config.temperature,
                thinking="off"
                if "off" in title_model_config.thinking_levels
                else title_model_config.thinking,
                top_p=title_model_config.top_p,
            )
            auto_title_enabled = _auto_titles_enabled(config, entrypoint)
            # The harness has its own built-in title prompt, written for its
            # transcript format. Send only a prompt the user configured.
            title_prompt = config.title_prompt
            if title_prompt == UtilityPrompt.SESSION_TITLE.read():
                title_prompt = None
            # Both routes run on the active model's provider.
            model_facts = compile_session_model_facts(
                provider,
                active_model,
                compaction_model,
                saved=saved_catalogue.snapshot,
                agent_max_tokens=config.agent_max_output_tokens or None,
            )
            derived_tools = ToolManager(
                lambda: config,
                defer_mcp=True,
                cwd=workspace.cwd,
                harness_files=harness_files,
            )
            available_tools = set(derived_tools.available_tools)
            command_tool_name = _command_tool_name(command_environment_mode)
            bash_timeout = _bash_timeout(
                command_tool_name, config.tools.get(command_tool_name)
            )
            bypass_approval = _effective_bypass_tool_permissions(
                config, options.auto_approve
            )
            # Recomputed on every derivation (including switch_agent) so entering or
            # leaving smart-approve mid-session re-derives the gate live.
            smart_approve = active_profile.name == BuiltinAgentName.SMART_APPROVE
            gate = (
                ToolGate.BYPASS
                if bypass_approval
                else ToolGate.CLASSIFIER
                if smart_approve
                else ToolGate.PROMPT
            )
            max_iterations = settings.max_turns or options.max_turns or 1_000
            session_plugins = plugins if settings.plugins is None else settings.plugins
            skill_issues, skills = discover_session_skills(
                lambda: orchestrator.config,
                harness_files=harness_files,
                plugin_skills=session_plugins.materialized.resolution.skills,
                plugin_contexts=core_plugins(session_plugins),
                skill_tool_available="skill" in available_tools,
                sandbox=sandbox_skills,
            )
            # Advertised here, bound by the plugin projection. Both sides measure
            # the same files against the same catalogue, so the names the model
            # reads are the names the Host can spawn.
            agent_types = resolve_agent_types(
                workspace_agent_types(agent_view),
                AgentToolCatalogue(
                    available=frozenset(available_tools),
                    permission_of=lambda name: (
                        derived_tools.get_tool_config(name).permission
                    ),
                    completion_route=completion_route,
                ),
                reserved=plugin_agent_names(
                    session_plugins.materialized.resolution.agents
                ),
            )
            profile_prompt, prompt_issue = _agent_profile_prompt(active_profile)
            return UnifiedRuntimeDerivation(
                runtime=build_unified_runtime_snapshot(
                    orchestrator,
                    agent_view,
                    issues=[
                        *plugin_issues(session_plugins),
                        *skill_issues,
                        *(
                            rejection.issue()
                            for rejection in workspace_agent_rejections(agent_view)
                        ),
                        *agent_types.issues,
                        *([prompt_issue] if prompt_issue is not None else []),
                        *_hook_config_issues(hook_result),
                        *plugin_hook_issues,
                        # A trusted home is both a project root and the user
                        # root: report each tool file once.
                        *ignored_tool_settings(
                            dict.fromkeys(
                                tool_dir.resolve()
                                for tool_dir in (
                                    *harness_files.project_tools_dirs,
                                    *harness_files.user_tools_dirs,
                                )
                            )
                        ),
                        *(
                            self._process_settings.issues(config)
                            if self._process_settings is not None
                            else []
                        ),
                    ],
                    skills=skills.catalogue,
                    tools=available_tools,
                    hooks_count=len(hook_result.hooks) + len(plugin_hooks),
                    auto_approve=options.auto_approve,
                    compaction_threshold=model_facts.agent.compaction_threshold,
                ),
                skill_payloads=skills.payloads,
                profile_warnings=model_facts.warnings,
                completion_catalogue=model_facts.catalogue,
                core_config=RustHarnessConfig(
                    task_id="runtime-template",
                    system_instructions=_build_unified_system_instructions(
                        config,
                        harness_files,
                        cwd=workspace.cwd,
                        base=options.system_prompt or profile_prompt,
                        extra=active_profile.instructions,
                        sandbox_workspace=sandbox_workspace,
                        sandbox_roots=sandbox_roots,
                        project_instructions=self._project_instructions,
                        background_processes=config.enable_background_processes,
                        command_environment=command_environment_mode,
                    ),
                    settings=RustHarnessSettings(
                        turn=RustTurnSettings(
                            max_iterations=max_iterations,
                            # A cut-off answer is discarded and asked for again
                            # with feedback, at most this many times a turn.
                            output_limit_recovery=(
                                RustRetryOutputLimitRecovery(
                                    max_retries=config.output_limit_retries
                                )
                                if config.output_limit_retries > 0
                                else RustDisabledOutputLimitRecovery()
                            ),
                        ),
                        context=RustContextSettings(
                            compaction=RustAutomaticCompactionPolicy(),
                            model_facts=rust_model_facts(model_facts.agent),
                            compaction_model_input_modalities=(
                                _compaction_model_input_modalities(
                                    model_facts.agent, model_facts.compaction
                                )
                            ),
                        ),
                        tools=RustToolSettings(
                            # The local Runtime answers `self.wait` from committed
                            # Session input (steering, child results, process
                            # completions). Core offers the direct `wait` tool only
                            # when subagents or background processes are enabled.
                            model_wait=RustEnabledModelWait(),
                            programmatic=RustProgrammaticToolSettings(
                                max_effects=128,
                                max_operations=1024,
                                max_elapsed_ms=60_000,
                                max_sleep_ms=30_000,
                            ),
                            subagents=(
                                RustEnabledAgentTools(conversation_forking=False)
                                if config.enable_subagents
                                else RustDisabledRuntimeToolFeature()
                            ),
                            background_processes=(
                                RustEnabledRuntimeToolFeature()
                                if config.enable_background_processes
                                else RustDisabledRuntimeToolFeature()
                            ),
                            command_environment=command_environment,
                            large_output=RustFilesystemLargeOutputPolicy(
                                max_output_tokens=_LARGE_OUTPUT_MAX_TOKENS,
                                model_visible_output_tokens=_LARGE_OUTPUT_VISIBLE_TOKENS,
                            ),
                            bash_timeout=bash_timeout,
                        ),
                    ),
                    capabilities=RustHarnessCapabilitySet(
                        tool_groups=[
                            *(
                                [
                                    RustToolGroupDefinition(
                                        name="ui",
                                        description="Interactive user interface tools",
                                        tools=[
                                            RustProvidedToolDefinition(
                                                name="ask_user_question",
                                                description=(
                                                    "Ask the user one or more questions and "
                                                    "wait for their answers."
                                                ),
                                                input_schema=UserQuestionRequest.model_json_schema(),
                                                output_schema=UserQuestionResult.model_json_schema(),
                                                exposure="direct",
                                            )
                                        ],
                                    )
                                ]
                                if "ask_user_question" in available_tools
                                else []
                            ),
                            *vibe_tool_groups(
                                available_tools,
                                enabled_tools=config.enabled_tools,
                                disabled_tools=config.disabled_tools,
                                session_spawn=(
                                    spawn_models(config)
                                    if session_spawner is not None
                                    else None
                                ),
                                session_peers=session_peers is not None,
                            ),
                            # The client chose these; the tool filters are for
                            # Vibe's own.
                            *self._client_tools.groups,
                        ],
                        skills=list(skills.definitions),
                        agent_types=list(agent_types.definitions),
                    ),
                    # No plugins: once a provider is configured its `bind` is
                    # the only source, and a template's would silently win over
                    # none.
                ),
                adapter_config=LocalRuntimeAdapterConfig(
                    provider=provider.name,
                    backend=str(provider.backend),
                    api_style=provider.api_style,
                    base_url=provider.api_base,
                    # A port, not a snapshot: a Vertex token expires inside a
                    # normal session's lifetime, and a re-exported key or a
                    # ``/config`` provider switch has no derivation to ride in
                    # on. Resolution happens at the model call instead.
                    credentials=credentials,
                    active_model=LocalModelRoute(
                        model=active_model.name,
                        temperature=active_model.temperature,
                        thinking=model_facts.agent_thinking,
                        top_p=active_model.top_p,
                        max_output_tokens=model_facts.agent.max_output_tokens,
                        output_room=_output_room(
                            model_facts.agent.context_window,
                            model_facts.agent.compaction_threshold,
                        ),
                        reasoning=model_facts.agent_reasoning,
                    ),
                    active_agent=active_profile.name,
                    compaction_model=LocalModelRoute(
                        model=compaction_model.name,
                        temperature=compaction_model.temperature,
                        thinking=model_facts.compaction_thinking,
                        top_p=compaction_model.top_p,
                        max_output_tokens=model_facts.compaction.max_output_tokens,
                        # Compaction input is the agent's context, which reaches
                        # the agent's threshold.
                        output_room=_output_room(
                            model_facts.compaction.context_window,
                            model_facts.agent.compaction_threshold,
                        ),
                        reasoning=model_facts.compaction_reasoning,
                    ),
                    title_model=title_model if auto_title_enabled else None,
                    title_prompt=title_prompt,
                    title_provider=_utility_provider_route(
                        title_provider_cfg,
                        session_provider_name=provider.name,
                        credentials=credentials_for(title_provider_cfg.name),
                        enabled=auto_title_enabled,
                    ),
                    # Its own route: the classifier may run on another provider.
                    # Set only under smart approve, when the gate runs.
                    classifier_provider=_utility_provider_route(
                        classifier_provider_cfg,
                        session_provider_name=provider.name,
                        credentials=credentials_for(classifier_provider_cfg.name),
                        enabled=smart_approve,
                    ),
                    # Set in every mode: a mid-turn switch into smart approve keeps
                    # the turn's model, which matches the turn's provider.
                    classifier_model=classifier_model_config.name,
                    max_tokens=settings.max_tokens,
                    # 0 is how a TOML file turns a limit off.
                    agent_max_tokens=config.agent_max_output_tokens or None,
                    compaction_max_tokens=config.compaction_max_output_tokens or None,
                    compaction_reasoning_limit_tokens=(
                        config.compaction_reasoning_limit_tokens or None
                    ),
                    subagent_max_children=config.max_subagents,
                    subagent_max_concurrent_turns=config.max_concurrent_subagents,
                    thinking=model_facts.agent_thinking,
                    reasoning_field_name=provider.reasoning_field_name,
                    emits_finish_reason=provider.emits_finish_reason,
                    extra_headers=with_user_agent(
                        provider.extra_headers, provider.backend
                    ),
                    project_id=provider.project_id,
                    region=provider.region,
                    timeout_s=config.api_timeout,
                    # 0 sets no separate idle limit: silence after the first
                    # output then gets the api_timeout wait too.
                    stream_idle_timeout_s=config.api_stream_idle_timeout or None,
                    # A budget of 0 turns retries off, the stall retry included.
                    retry_max_elapsed_time_s=config.api_retry_max_elapsed_time,
                    enable_system_trust_store=config.enable_system_trust_store,
                    workspace=workspace,
                    # The library environment goes on top: a plugin that declares a
                    # Python or Node library is only usable if the search paths
                    # materialization computed reach the process that runs a
                    # command. Materialization already folded the inherited
                    # PYTHONPATH and NODE_PATH into those values, so overriding is
                    # prepending, not discarding.
                    # In a sandbox, commands run with the sandbox's own
                    # environment: these search paths name host directories.
                    env=(
                        {
                            **os.environ,
                            **session_plugins.materialized.process_environment,
                        }
                        if sandbox is None
                        else {}
                    ),
                    command_environment=command_environment_mode,
                    shell=getattr(
                        derived_tools.get_tool_config(command_tool_name), "shell", None
                    ),
                    process_authority=(
                        "host_shell" if workspace.path_space == "host" else "sandbox"
                    ),
                    bypass_approval=bypass_approval,
                    tool_modes=_rust_tool_modes(
                        available_tools,
                        lambda name: derived_tools.get_tool_config(name).permission,
                        lambda name: derived_tools.get_tool_config(name).allowlist,
                        gate=gate,
                    ),
                    provided_tool_mode=_rust_provided_tool_mode(gate),
                    permission_resolver=permission_resolver.resolve,
                    skills=skills.model_payloads,
                    correlation_id_sink=correlation.record,
                    request_sent_sink=subagent_steps.counting(request_sent.record),
                    malformed_tool_calls_sink=malformed_tool_calls.record,
                    sandbox=sandbox,
                    helper_crashes=helper_crashes,
                ),
            )

        def derive(settings: UnifiedSessionSettings) -> UnifiedRuntimeDerivation:
            return derive_config(config_orchestrator.config, settings)

        async def preflight(
            config: VibeConfigSchema,
            settings: UnifiedSessionSettings,
            candidate: UnifiedConfigCandidate | None = None,
        ) -> UnifiedRuntimeDerivation:
            derivation = await asyncio.to_thread(
                derive_config, config, settings, candidate
            )

            def validate_core_config() -> None:
                core = HarnessSession.create(
                    derivation.core_config.model_dump_json(exclude_none=True), "[]"
                )
                core.close()

            await asyncio.to_thread(validate_core_config)
            return derivation

        # Compile the discovered hooks into Core bindings + Runtime handlers. The
        # catalogue provider is lazy: it only runs the native call when a hook
        # actually scopes itself to a tool by name. Deriving from default settings is
        # safe here: only max_iterations/max_tokens vary by UnifiedSessionSettings, and
        # neither changes core_config's tool set, so the catalog is settings-invariant.
        core_config_json = derive(UnifiedSessionSettings()).core_config.model_dump_json(
            exclude_none=True
        )
        hooks = await asyncio.to_thread(
            compile_foreign_hooks,
            _foreign_hook_definitions(
                hook_result,
                harness_files=harness_files,
                cwd=cwd,
                plugin_hooks=plugin_hooks,
                session_plugins=plugin_provider.first_bound,
            ),
            tool_catalog=lambda: tool_catalog_for_config(core_config_json),
        )
        # Builtin hooks that the Core must bind for every unified session; the
        # lazy AGENTS.md injection restores the legacy read_file's discovery of
        # subdirectory docs (see _agents_md_hooks). The handler is registered
        # Host-globally, so only the binding rides the session here.
        hooks = merge_agents_md_hook(hooks)
        scoped_post_agent_hooks, main_post_agent_hooks = _post_agent_hook_counts(
            hook_result, plugin_hooks
        )
        return UnifiedSessionContext(
            storage_root=self._unified_storage_root(config.session_logging),
            session_logging_enabled=config.session_logging.enabled,
            legacy_source_loader=legacy_store.loader(),
            legacy_source_resolver=legacy_store.resolver(),
            plugins=plugins,
            plugin_provider=plugin_provider,
            plugin_mcp=plugin_mcp,
            requested_plugins=tuple(requested_plugin_definitions(plugins)),
            config_orchestrator=config_orchestrator,
            harness_files=harness_files,
            agents=agents,
            org_config_service=self.services.org_config,
            org_config_key=OrgConfigKeySlot(session_config.org_config_key),
            derive=derive,
            permissions=permission_resolver,
            provided_names=provided_names,
            client_tools=self._client_tools,
            session_spawner=session_spawner,
            session_peers=session_peers,
            hooks=hooks,
            post_agent_scoped_hooks_count=scoped_post_agent_hooks,
            post_agent_main_hooks_count=main_post_agent_hooks,
            mcp_catalog=mcp_catalog,
            mcp_authorization_provider=self.mcp_authentication,
            mcp_authentication=self.mcp_authentication,
            mcp_catalog_service=self.mcp_catalog,
            mcp_cache_root=str(
                Path(config.session_logging.save_dir).expanduser().resolve().parent
                / "mcp-descriptors"
                / "unified"
            ),
            mcp_enable_system_trust_store=config.enable_system_trust_store,
            connector_catalog=connector_catalog,
            connector_selection=connector_selection,
            connector_catalog_service=self.connector_catalog,
            connector_base_url=connector_base_url,
            connector_api_key=connector_api_key,
            preflight=preflight,
            # Built here rather than defaulted on the context: the default
            # ``ExperimentManager`` has no eval URL, so every resolution would
            # be a silent no-op.
            experiment_manager=ExperimentManager(
                client=RemoteEvalClient.from_settings(
                    api_host=config.experiments.api_host,
                    client_key=config.experiments.client_key,
                )
            ),
            correlation=correlation,
            request_sent=request_sent,
            malformed_tool_calls=malformed_tool_calls,
            subagent_steps=subagent_steps,
            saved_catalogue=saved_catalogue,
        )

    def _session_cwd(self, options: SessionOptions) -> Path:
        if self._sandbox is not None:
            from mistralai_vibe_local_harness.vibe import sandbox_path

            workspace = self._sandbox.workspace
            return sandbox_path(options.cwd or workspace, cwd=workspace)
        return Path(options.cwd or Path.cwd()).expanduser().resolve()

    async def _build_session_config(
        self,
        options: SessionOptions,
        *,
        org_config: OrgConfigSource = OrgConfigSource.CACHED,
        check_process_settings: bool = True,
    ) -> _SessionConfig:
        """``check_process_settings=False`` leaves the process-settings check to
        the caller: a session build runs it where it can fall back.
        """
        if self._agent_socket is not None:
            self._agent_socket.check()
        cwd = self._session_cwd(options)
        overrides = _session_config_overrides(options)
        if self._sandbox is None:
            workspace_roots = [
                Path(root).expanduser().resolve() for root in options.workspace_roots
            ]
            # The session cwd is deliberately absent from workspace_roots here:
            # a listed cwd becomes a session root, and ``project_roots`` returns
            # the listed roots even when the tree is untrusted, which would load
            # that tree's AGENTS.md past the trust gate. The same invariant is
            # enforced by the AGENTS.md hook handler (see
            # ``_agents_md_hooks._session_files``); the durable home would be
            # ``for_session`` itself ignoring a listed root equal to the cwd.
            harness_files = self.harness_files.for_session(
                cwd, workspace_roots=workspace_roots
            )
            if options.trust_workspace:
                harness_files.trust_store.trust_for_session(cwd)
        else:
            # Only user-level files load: the project's own config, skills and
            # hooks are in the sandbox, and the host's disk at the same path is
            # not them.
            harness_files = self.harness_files.for_session(cwd)
        # A session start applies the org config the process holds, and only
        # waits for one when it holds none: an old one is refreshed in the
        # background, and each open session applies the answer at its next
        # turn boundary. Reads outside a session never fetch, except the
        # picker's config reads when the cache holds nothing.
        org_service = (
            None if org_config is OrgConfigSource.NONE else self.services.org_config
        )
        fetch_outcome = None

        async def build() -> BuiltSessionConfig:
            nonlocal fetch_outcome
            config_orchestrator = await build_default_orchestrator(
                overrides, harness_files=harness_files
            )
            pre_org_config = config_orchestrator.config
            if org_config is OrgConfigSource.ENSURED:
                ensured_key = await org_config_key(config_orchestrator)
                fetch_outcome = (
                    OIDC_TENANT_UNRESOLVED
                    if ensured_key is None
                    and oidc_tenant_unresolved(config_orchestrator.config)
                    else await self.services.org_config.ensure(ensured_key)
                )
            org_key, tried, saved_failure = await apply_cached_layers(
                config_orchestrator, org_service
            )
            return BuiltSessionConfig(
                config_orchestrator,
                pre_org_config=pre_org_config,
                org_config_key=org_key,
                org_config_tried=tried,
                org_config_failure=saved_failure,
            )

        try:
            # A start can wait on its org config, so it is never answered
            # from a build made without that wait.
            built = (
                await build()
                if org_config is OrgConfigSource.ENSURED
                else await self._session_configs.get(
                    SessionConfigKey.of(
                        cwd, overrides, org_config=org_service is not None
                    ),
                    harness_files,
                    org_service,
                    build,
                )
            )
        except ValidationError as exc:
            raise InvalidConfigError.from_validation_error(exc) from exc
        config_orchestrator = built.config_orchestrator
        # What the build set the process's TLS to, before the org config, set
        # again for a reused build: the process follows the latest config.
        configure_ssl_context(
            enable_system_trust_store=built.pre_org_config.enable_system_trust_store
        )
        org_key = built.org_config_key
        tried = built.org_config_tried
        saved_failure = built.org_config_failure
        # After the org config: it can set the process-wide trust store too.
        if check_process_settings and self._process_settings is not None:
            self._process_settings.check(config_orchestrator.config)
        # Every session crosses this config build, and the unified harness
        # never constructs the legacy agent loop, so the sweep starts here.
        start_restrict_session_log_permissions(
            config_orchestrator.config.session_logging
        )
        return _SessionConfig(
            config_orchestrator=config_orchestrator,
            harness_files=harness_files,
            org_config_key=org_key,
            org_config=OrgConfigAttempt(
                org_config,
                saved=tried if saved_failure is None else None,
                fetch_outcome=fetch_outcome,
                saved_failure=saved_failure,
                considered=tried,
            ),
        )

    async def _build_plugins(
        self,
        session_config: _SessionConfig,
        cwd: Path,
        *,
        agents: AgentManager,
        subagent_permissions: SubagentPermissions,
        completion_route: Callable[[Mapping[str, Any]], DeclaredCompletionRoute | None],
    ) -> tuple[SessionPlugins, UnifiedPluginProvider, PluginMCPCatalog]:
        """The Host's own resolve, and the provider the Runtime binds through.

        One call, because both need the same MCP catalog and connector registry
        and building them twice would open two MCP client pools for one session.
        """
        from vibe.app_server._plugins import (
            UnifiedPluginProvider,
            installed_plugin_scopes,
            resolve_session_plugins,
        )

        config = session_config.config_orchestrator.config
        plugin_mcp = self._build_plugin_mcp_catalog(config)
        connector_registry = await self._build_connector_registry(
            session_config.config_orchestrator
        )
        plugins = await resolve_session_plugins(
            session_config.harness_files,
            config_orchestrator=session_config.config_orchestrator,
            plugin_mcp=plugin_mcp,
            connector_registry=connector_registry,
            builtin_plugin_roots=self._builtin_plugin_roots,
        )

        def agent_tools() -> AgentToolCatalogue:
            # Re-derived per bind rather than captured: a ``config/write`` re-binds,
            # and a ceiling measured against the catalogue the session started with
            # would keep denying a child a tool the user has since enabled.
            current = session_config.config_orchestrator.config
            tools = ToolManager(
                lambda: current,
                defer_mcp=True,
                cwd=cwd,
                harness_files=session_config.harness_files,
            )
            return AgentToolCatalogue(
                available=frozenset(tools.available_tools),
                permission_of=lambda name: tools.get_tool_config(name).permission,
                permission_resolver=subagent_permissions.resolver_for,
                completion_route=completion_route,
            )

        return (
            plugins,
            UnifiedPluginProvider(
                storage_root=Path(config.session_logging.save_dir),
                workdir=session_config.harness_files.cwd or cwd,
                installed_roots={
                    plugin.name: plugin.root
                    for plugin in plugins.materialized.resolution.plugins
                },
                installed_scopes=installed_plugin_scopes(plugins),
                config_orchestrator=session_config.config_orchestrator,
                plugin_mcp=plugin_mcp,
                connector_registry=connector_registry,
                # Kept so ``plugin/reload`` repeats this resolve. A rescan that
                # walked elsewhere would report the session's plugins uninstalled.
                harness_files=session_config.harness_files,
                agent_tools=agent_tools,
                builtin_plugin_roots=self._builtin_plugin_roots,
                # Read per bind for the same reason as the catalogue above: an
                # agent file edited mid-session reaches the next bind.
                agent_types=lambda: workspace_agent_types(agents),
                agent_rejections=lambda: workspace_agent_rejections(agents),
                sandbox_skills=self._sandbox_skills,
            ),
            plugin_mcp,
        )

    def _build_plugin_mcp_catalog(self, config: VibeConfigSchema) -> PluginMCPCatalog:
        # Its own registry and descriptor cache: plugin sources are discovered
        # under private aliases and must not collide with the config-owned
        # catalogue. The auth service behind both is the same.
        from vibe.app_server._plugin_mcp import PluginMCPCatalog
        from vibe.core.tools.mcp.registry import MCPRegistry

        cache_root = (
            Path(config.session_logging.save_dir).expanduser().resolve().parent
            / "mcp-descriptors"
            / "plugins"
        )
        return PluginMCPCatalog(
            MCPRegistry(descriptor_cache_root=cache_root),
            self.mcp_authentication,
            descriptor_cache_root=cache_root,
        )

    async def _build_connector_registry(
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema]
    ) -> ConnectorRegistry | None:
        from vibe.core.tools.connectors.connector_registry import (
            ConnectorAuthAction,
            ConnectorCatalogEntry,
            ConnectorRegistry,
            ConnectorToolDefinition,
        )
        from vibe.utils.http import get_server_url_from_api_base

        provider = orchestrator.config.get_mistral_provider()
        if provider is None:
            return None
        api_key_env = provider.api_key_env_var or "MISTRAL_API_KEY"
        api_key = resolve_api_key(api_key_env)
        if not api_key:
            return None
        server_url = get_server_url_from_api_base(provider.api_base)
        # The cache only: opening a session never waits on the connector
        # service. With nothing cached, plugin connectors resolve as unavailable,
        # as an unreachable service does.
        catalog = await self.connector_catalog.read_cached_catalog(orchestrator)
        entries = (
            tuple(
                ConnectorCatalogEntry(
                    connector_id=connector.raw_id,
                    alias=connector.alias,
                    display_name=connector.display_name,
                    ready=connector.ready,
                    auth_action=(
                        ConnectorAuthAction(connector.auth_action)
                        if connector.auth_action != "unknown"
                        else ConnectorAuthAction.NONE
                    ),
                    tools=tuple(
                        ConnectorToolDefinition(
                            name=tool.raw_name,
                            description=tool.description,
                            input_schema=dict(tool.input_schema),
                        )
                        for tool in connector.tools
                    ),
                    diagnostic="; ".join(connector.diagnostics) or None,
                )
                for connector in catalog.connectors
            )
            if catalog is not None
            else ()
        )
        return ConnectorRegistry(
            api_key=api_key, server_url=server_url, catalog_entries=entries
        )


async def create_harness_server(
    transport: JsonRpcTransport,
    *,
    transport_kind: TransportKind,
    process: HarnessProcess,
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
) -> HarnessServer:
    """Build a server over ``transport``.

    ``account_gateway``/``identity_gateway`` reach the platform API and are left
    unset in production, where ``AppServer`` builds the HTTP ones. They exist so
    a test can drive the account and identity surfaces over either backend
    without a network.
    """
    from vibe.app_server.server import AppServer

    return HarnessServer(
        _server=AppServer(
            transport,
            transport_kind=transport_kind,
            host_handler=process.host_handler,
            session_backend_host_factory=process.create_session_backend_host,
            mcp_catalog_service=process.mcp_catalog,
            connector_catalog_service=process.connector_catalog,
            plugin_catalog_service=process.plugin_catalog,
            imports_service=process.imports,
            account_gateway=account_gateway,
            identity_gateway=identity_gateway,
        ),
        _transport=transport,
        _reconnectable=transport_kind == "in_process",
    )


def _hook_config_issues(result: HookConfigResult) -> list[ConfigIssue]:
    """Project hooks.toml parse/duplicate diagnostics onto the session's issues.

    ``load_hooks_from_fs`` skips an invalid or conflicting file's hooks and records why
    on ``result.issues``. Surfacing them as session notices (rather than dropping them
    silently) matches the legacy loop, which projects ``hook_config_issues``, and the
    design's failure table ("CLI logs a diagnostic; that file's hooks are skipped").
    """
    return [
        ConfigIssue(file=str(issue.file), message=issue.message)
        for issue in result.issues
    ]


def _user_hook_names(harness_files: HarnessFilesManager) -> set[str]:
    """Names of hooks that come from ``~/.vibe/hooks.toml``, after project-first dedup.

    ``load_hooks_from_fs`` merges project and user files (project first) and drops the
    origin, so recover it here: a surviving hook is user-owned only when it is declared in
    the user file and by no project file.
    """
    user_file = (VIBE_HOME.path / "hooks.toml").resolve()
    project_names: set[str] = set()
    user_names: set[str] = set()
    for path in harness_files.hook_files:
        names = {hook.name for hook in load_hooks_file(path).hooks}
        if path.resolve() == user_file:
            user_names |= names
        else:
            project_names |= names
    return user_names - project_names


def _foreign_hook_definitions(
    result: HookConfigResult,
    *,
    harness_files: HarnessFilesManager,
    cwd: Path,
    plugin_hooks: Sequence[RuntimeHookDefinition] = (),
    session_plugins: Callable[[str], SessionPlugins | None] | None = None,
) -> list[Any]:
    """Map the session's hook declarations to harness hook definitions, in run order.

    ``result.hooks`` are the parsed ``[[hooks]]`` of the project and user
    ``hooks.toml``; ``plugin_hooks`` are those native plugins ship. They run project
    file, project plugins, user file, then global plugins (``HookSource`` order).

    ``source`` scopes the binding id (``f"{source}:{name}"``). A user hook is labelled
    ``"user"`` and a project hook the session cwd, so a persisted project binding cannot be
    matched by a same-named user hook on a resume where project trust has since been lost.
    A plugin hook is named ``<plugin>:<hook>`` and labelled ``plugin:user`` when the plugin
    is global, ``plugin:<cwd>`` when it ships with the project, for the same reason.

    A plugin hook runs against the plugin files the session pinned, like the plugin's
    MCP servers: ``session_plugins`` names them per run, from the session id. It is
    the set the session started with, so a hook's files stay those of the command it
    was compiled from after a ``/reload-plugins``. The installed plugin is only the
    fallback for a session with no plugins bound.
    """
    from mistralai_vibe_local_harness.vibe import ForeignHookDefinition, HookProcess

    user_names = _user_hook_names(harness_files)
    cwd_source = str(cwd)

    def from_file(hook: HookConfig) -> Any:
        source = "user" if hook.name in user_names else cwd_source
        return ForeignHookDefinition(
            name=hook.name,
            point=hook.type.value,
            command=hook.command,
            source=source,
            match=hook.match,
            timeout_s=hook.timeout if hook.timeout is not None else 60.0,
            strict=hook.strict,
        )

    def from_plugin(hook: RuntimeHookDefinition) -> Any:
        scope = cwd_source if hook.source is HookSource.PROJECT_PLUGIN else "user"

        def process(context: HookContext) -> Any:
            # A subagent that runs without integrations binds no plugins of its own;
            # its hooks then follow the parent's.
            bound = None
            if session_plugins is not None:
                bound = session_plugins(context.session_id)
                if bound is None and context.parent_session_id:
                    bound = session_plugins(context.parent_session_id)
            pinned = _pinned_plugin_hook(bound, hook)
            return HookProcess(cwd=pinned.cwd, env=dict(pinned.environment))

        return ForeignHookDefinition(
            name=hook.config.name,
            point=hook.config.type.value,
            command=hook.config.command,
            source=f"plugin:{scope}",
            match=hook.config.match,
            timeout_s=hook.config.timeout if hook.config.timeout is not None else 60.0,
            strict=hook.config.strict,
            process=process,
        )

    file_hooks = [from_file(hook) for hook in result.hooks]
    ordered = [
        *(hook for hook in file_hooks if hook.source == cwd_source),
        *(
            from_plugin(h)
            for h in plugin_hooks
            if h.source is HookSource.PROJECT_PLUGIN
        ),
        *(hook for hook in file_hooks if hook.source == "user"),
        *(from_plugin(h) for h in plugin_hooks if h.source is HookSource.GLOBAL_PLUGIN),
    ]
    return [replace(hook, order=index) for index, hook in enumerate(ordered)]


def _pinned_plugin_hook(
    bound: SessionPlugins | None, hook: RuntimeHookDefinition
) -> RuntimeHookDefinition:
    """The session's pinned copy of ``hook``, which names its checkout and data dir.

    Matched by plugin and declared name. A plugin a reload dropped, or a session with
    no plugins bound, keeps the hook as resolved at start.
    """
    if bound is None:
        return hook
    return next(
        (
            candidate
            for candidate in bound.materialized.resolution.runtime_hooks
            if candidate.plugin_name == hook.plugin_name
            and candidate.declared_name == hook.declared_name
        ),
        hook,
    )


def _runnable_plugin_hooks(
    plugins: SessionPlugins,
) -> tuple[list[RuntimeHookDefinition], list[ConfigIssue]]:
    """The plugin hooks Vibe can run, and a notice for each one it cannot.

    Only hooks written for Vibe run: a hook a Claude Code or Kimi plugin declared speaks
    that tool's protocol (exit code 2 to block, camelCase fields), and running it as a
    Vibe hook would let a blocking guard silently allow everything.
    """
    runnable: list[RuntimeHookDefinition] = []
    skipped: dict[tuple[str, HookProtocol], list[RuntimeHookDefinition]] = {}
    for hook in plugins.materialized.resolution.runtime_hooks:
        if hook.protocol is HookProtocol.VIBE:
            runnable.append(hook)
            continue
        skipped.setdefault((hook.plugin_name or "", hook.protocol), []).append(hook)
    issues = [
        ConfigIssue(
            file=next(
                (str(hook.config_file) for hook in hooks if hook.config_file), plugin
            ),
            message=(
                f"Plugin {plugin!r} declares {len(hooks)} hook(s) written for "
                f"{protocol.value}; they do not run in Vibe yet"
            ),
        )
        for (plugin, protocol), hooks in skipped.items()
    ]
    return runnable, issues


def _post_agent_hook_counts(
    result: HookConfigResult, plugin_hooks: Sequence[RuntimeHookDefinition] = ()
) -> tuple[int, int]:
    """Count the scoped post_agent hooks (a ``match`` other than ``*``), and those
    selecting ``main``, across ``hooks.toml`` files and runnable plugin hooks.

    ``main`` is tested the way the Runtime tests a main session with no agent name, so a
    broad pattern such as ``re:.*`` counts in both.
    """
    from mistralai_vibe_local_harness.vibe._hook_matcher import (
        is_unscoped,
        selects_agent,
    )

    configs = [*result.hooks, *(hook.config for hook in plugin_hooks)]
    scoped = [
        hook.match
        for hook in configs
        if hook.type is HookType.POST_AGENT and not is_unscoped(hook.match)
    ]
    targeting_main = [
        match for match in scoped if selects_agent(match, None, main=True)
    ]
    return len(scoped), len(targeting_main)


def _effective_bypass_tool_permissions(
    config: VibeConfigSchema, requested: bool
) -> bool:
    """Resolve the tool-approval bypass from the session flag and the config.

    ``requested`` is the session-wide ``--auto-approve``/``--yolo`` option and
    ``config.bypass_tool_permissions`` covers the config path (including the
    auto-approve agent's profile override). A policy setting that disables the
    auto-approve mode (``enable_auto_approve = false``) clamps both: the flag is
    a request, not a right.
    """
    return (requested or config.bypass_tool_permissions) and config.enable_auto_approve


def build_runtime_snapshot(
    options: SessionOptions,
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema],
    harness_files: HarnessFilesManager,
    *,
    issues: Sequence[ConfigIssue] = (),
) -> RuntimeSnapshot:
    agents = AgentManager(
        config_orchestrator, options.agent, harness_files=harness_files
    )
    return build_unified_runtime_snapshot(
        config_orchestrator, agents, issues=issues, auto_approve=options.auto_approve
    )


def build_unified_runtime_snapshot(
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema],
    agents: AgentManager,
    *,
    issues: Sequence[ConfigIssue] = (),
    skills: Iterable[SkillInfo] = (),
    tools: Iterable[str] = (),
    hooks_count: int = 0,
    auto_approve: bool = False,
    compaction_threshold: int | None = None,
) -> RuntimeSnapshot:
    """Project the layered config into the runtime state the client observes.

    The tools are the resolved Vibe catalogue used to derive the local Runtime's
    executable tool modes. ``skills`` and ``issues`` are supplied by the caller
    because they come from discovery and plugin resolution, not from the layered
    config. ``hooks_count`` is supplied by the caller because hook discovery
    happens outside the layered config. ``compaction_threshold`` is the active
    route's compiled one, which the client shows context use against; a caller
    that has not compiled the route yet leaves it unset to compile it here.
    Integration projections are filled by their owning adapters.
    """
    config = config_orchestrator.config
    active_model = config.get_active_model()
    active, available = project_agent_summaries(
        agents.active_profile, agents.available_agents.values()
    )
    runtime_issues = list(issues)
    if auto_approve and not config.enable_auto_approve:
        runtime_issues.append(
            ConfigIssue(
                file="--auto-approve",
                message=(
                    "Auto-approve was requested (--auto-approve/--yolo) but the mode "
                    "is disabled (enable_auto_approve = false); approval prompts stay "
                    "on."
                ),
            )
        )
    if compaction_threshold is None:
        from vibe.app_server._completion_facts import compile_agent_compaction_threshold

        compaction_threshold = compile_agent_compaction_threshold(
            config.get_provider_for_model(active_model), active_model
        )
    return RuntimeSnapshot(
        config=project_config_view(
            config,
            active_model_pinned=active_model_is_pinned(config_orchestrator),
            image_fallback=True,
        ),
        active_agent=active,
        agents=available,
        skills=project_skill_summaries(skills),
        tools=[ToolSummary(name=name) for name in tools],
        stats=AgentStatsSnapshot(
            input_price_per_million=active_model.input_price,
            output_price_per_million=active_model.output_price,
            cached_input_price_per_million=active_model.cached_input_price,
        ),
        context_window=compaction_threshold,
        issues=runtime_issues,
        hooks_count=hooks_count,
        connectors=ConnectorCounts(),
        mcp=MCPState(),
        bypass_tool_permissions=_effective_bypass_tool_permissions(
            config, auto_approve
        ),
    )


_RUST_MODE_BY_PERMISSION: dict[ToolPermission, Literal["allow", "ask", "deny"]] = {
    ToolPermission.ALWAYS: "allow",
    ToolPermission.ASK: "ask",
    ToolPermission.NEVER: "deny",
}
# Strictest first. One Rust builtin can stand for several Vibe tools, and the
# single mode it gets has to hold for whichever of them the Runtime runs.
_PERMISSION_STRICTNESS: tuple[ToolPermission, ...] = (
    ToolPermission.NEVER,
    ToolPermission.ASK,
    ToolPermission.ALWAYS,
)


class ToolGate(StrEnum):
    """How a session approves a gated builtin tool call, resolved from the mode."""

    PROMPT = auto()
    BYPASS = auto()
    CLASSIFIER = auto()


def _rust_tool_modes(
    available_tools: set[str],
    permission_of: Callable[[str], ToolPermission],
    allowlist_of: Callable[[str], Sequence[str]],
    *,
    gate: ToolGate,
) -> dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny", "classify"]]:
    """Map the effective Vibe tool catalogue onto Rust builtin approval modes.

    A builtin is denied when the catalogue offers no tool it stands for, so a
    ``config/write`` that removes every shell tool also stops the Runtime from
    running commands — but a platform that merely spells the shell differently
    does not.

    A present builtin takes the strictest Vibe permission across its sources.
    Following main's per-call model, a builtin that would ``allow`` is lowered to
    ``ask`` so the Host's permission resolver applies its rules (the ``.env``
    prompt, path/command denylists, allowlist grants) per call; ``BYPASS`` keeps
    ``allow`` and retires the resolver. ``never`` is raised to ``ask`` for the same
    reason when an allowlist is the exception to it (see ``_allowlist_exempts``).
    Under ``CLASSIFIER``
    (smart approve) every builtin that would ``ask`` becomes ``classify``: the
    classify gate runs the resolver first and only classifies its ``ask`` residue.
    ``classify`` rides the mutable adapter config, so switching modes flips it live.
    """
    from vibe.app_server._unified_permissions import RUST_BUILTIN_TOOL_SOURCES

    bypass_approval = gate is ToolGate.BYPASS
    configured: dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny"]] = {}
    for builtin, sources in RUST_BUILTIN_TOOL_SOURCES.items():
        present = sources & available_tools
        if not present:
            configured[builtin] = "deny"
        elif bypass_approval:
            configured[builtin] = "allow"
        else:
            permissions = {permission_of(name) for name in present}
            strictest = next(
                permission
                for permission in _PERMISSION_STRICTNESS
                if permission in permissions
            )
            configured[builtin] = (
                "ask"
                if _allowlist_exempts(builtin, present, permission_of, allowlist_of)
                else _RUST_MODE_BY_PERMISSION[strictest]
            )

    # A builtin that would `allow` becomes `ask` so the per-call resolver applies
    # its rules; `deny`/`ask` stand.
    modes: dict[
        RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny", "classify"]
    ] = {
        builtin: "ask" if mode == "allow" and not bypass_approval else mode
        for builtin, mode in configured.items()
    }
    # Smart approve layers the classifier on the resolver: every builtin that would
    # `ask` becomes `classify`. The classify gate runs the resolver first, so
    # main's per-call rules still apply beneath it. `allow`/`deny` stand.
    if gate is ToolGate.CLASSIFIER:
        modes = {
            builtin: "classify" if mode == "ask" else mode
            for builtin, mode in modes.items()
        }
    return modes


def _rust_provided_tool_mode(
    gate: ToolGate,
) -> Literal["allow", "ask", "deny", "classify"]:
    """Mode gating provided/MCP tools, which have no per-name entry in tool_modes."""
    match gate:
        case ToolGate.PROMPT:
            return "ask"
        case ToolGate.CLASSIFIER:
            return "classify"
        case ToolGate.BYPASS:
            return "allow"


def rust_agent_tool_ceiling(
    available_tools: set[str],
    permission_of: Callable[[str], ToolPermission],
    overrides: Mapping[str, Any],
) -> dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny"]]:
    # A ceiling, never a grant: the Runtime keeps the stricter of this and the mode
    # the parent holds, so the worst a wrong answer here does is deny a child
    # something it was allowed. ``allowlist``/``denylist`` are not carried as
    # patterns: they are per-call path and command policy, which the child's own
    # resolver answers under the profile's rules (``SubagentPermissions``).
    # ``_rust_tool_modes``' ``never``-plus-allowlist lift has no counterpart here.
    # Under an auto-approve parent the child's ``ask`` runs unasked, so the lift
    # would turn the refusal the profile asked for into an unattended write
    # anywhere.
    narrowed = _profile_tools(available_tools, overrides)
    per_tool = overrides.get("tools")
    per_tool = per_tool if isinstance(per_tool, Mapping) else {}

    def permission(name: str) -> ToolPermission:
        return _declared_permission(per_tool.get(name), name, permission_of)

    # The ceiling encodes what the profile *permits* (allow/ask/deny), not what
    # the session's approval mode would do. The Runtime takes the stricter of
    # this and the parent's mode, so ``allow`` here is safe: under PROMPT the
    # parent's ``ask`` is stricter and the resolver still runs; under BYPASS the
    # parent's ``allow`` lets the child bypass — the user's explicit choice, not
    # something a plugin profile can grant on its own. A ceiling never classifies.
    from vibe.app_server._unified_permissions import RUST_BUILTIN_TOOL_SOURCES

    ceiling: dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny"]] = {}
    for builtin, sources in RUST_BUILTIN_TOOL_SOURCES.items():
        present = sources & narrowed
        if not present:
            ceiling[builtin] = "deny"
        else:
            permissions = {permission(name) for name in present}
            strictest = next(
                perm for perm in _PERMISSION_STRICTNESS if perm in permissions
            )
            ceiling[builtin] = _RUST_MODE_BY_PERMISSION[strictest]

    return ceiling


def _profile_tools(available_tools: set[str], overrides: Mapping[str, Any]) -> set[str]:
    """The tools a profile leaves its child, before permissions are read."""
    enabled = _glob_patterns(overrides.get("enabled_tools"))
    disabled = _glob_patterns(overrides.get("disabled_tools"))
    return {
        name
        for name in available_tools
        if (not enabled or name_matches(name, enabled))
        and not (disabled and name_matches(name, disabled))
    }


def _declared_permission(
    entry: Any, name: str, permission_of: Callable[[str], ToolPermission]
) -> ToolPermission:
    declared = entry.get("permission") if isinstance(entry, Mapping) else None
    if not isinstance(declared, str):
        return permission_of(name)
    try:
        return ToolPermission(declared)
    except ValueError:
        return permission_of(name)


def _allowlist_exempts(
    builtin: RustRuntimeBuiltinToolName,
    present: AbstractSet[str],
    permission_of: Callable[[str], ToolPermission],
    allowlist_of: Callable[[str], Sequence[str]],
) -> bool:
    """Whether a ``never`` still has to reach the resolver instead of denying here.

    The Runtime's ``deny`` short-circuits the permission resolver, and the allowlist
    that excuses a call lives inside it, so collapsing ``never`` to ``deny`` turns
    "never, except these paths" into an absolute refusal. ``ask`` puts the resolver
    back in the path, and it denies everything the allowlist does not cover.
    """
    from vibe.app_server._unified_permissions import ALLOWLIST_EXEMPTED_BUILTINS

    if builtin not in ALLOWLIST_EXEMPTED_BUILTINS:
        return False
    return any(
        permission_of(name) is ToolPermission.NEVER and allowlist_of(name)
        for name in present
    )


def _glob_patterns(value: Any) -> list[str]:
    # Overrides come from plugin-authored TOML, so a malformed entry narrows nothing
    # rather than failing the bind.
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _project_session_mcp_server(server: SessionMCPServer) -> MCPServer:
    match server:
        case SessionMCPHttpServer(transport="http"):
            return MCPHttp(
                transport="http",
                name=server.name,
                url=server.url,
                auth=MCPStaticAuth(headers=server.headers),
            )
        case SessionMCPHttpServer(transport="streamable-http"):
            return MCPStreamableHttp(
                transport="streamable-http",
                name=server.name,
                url=server.url,
                auth=MCPStaticAuth(headers=server.headers),
            )
        case SessionMCPStdioServer():
            return MCPStdio(
                transport="stdio",
                name=server.name,
                # The session protocol already separates the executable from its args.
                command=[server.command],
                args=server.args,
                env=server.env,
                cwd=server.cwd,
            )
        case _:
            raise TypeError(f"Unsupported session MCP server: {type(server).__name__}")


async def _accept_org_config(context: UnifiedSessionContext) -> None:
    """Check the org config a context was built under.

    Built is not enough: the Core must accept the config too, or the failure
    would only surface when the session starts, past every fallback. Raises
    when the Core rejects it.
    """
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    orchestrator = context.config_orchestrator
    if enforced_keys(orchestrator) and context.preflight is not None:
        await context.preflight(orchestrator.config, UnifiedSessionSettings())


def _session_config_overrides(options: SessionOptions) -> dict[str, object]:
    overrides: dict[str, object] = {}
    if options.enabled_tools is not None:
        overrides["enabled_tools"] = options.enabled_tools
    if options.disabled_tools:
        overrides["disabled_tools"] = options.disabled_tools
    if options.mcp_servers:
        overrides["mcp_servers"] = [
            _project_session_mcp_server(server).model_dump(
                mode="json", exclude_none=True
            )
            for server in options.mcp_servers
        ]
    return overrides
