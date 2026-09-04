from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any, Literal

from pydantic import JsonValue, ValidationError

from vibe import __version__
from vibe._experimental_harness import (
    ExperimentalHarnessUnavailableError,
    create_experimental_harness_host,
)
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._projection import (
    project_agent_summaries,
    project_config_view,
    project_skill_summaries,
)
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server._session_model import (
    active_model_is_pinned,
    clear_session_active_model_override,
    config_active_model,
    set_session_active_model_override,
)
from vibe.app_server._skills import discover_session_skills
from vibe.app_server.client import AppServerClient
from vibe.app_server.client_tools import ClientToolHandler
from vibe.app_server.connector_catalog import (
    ConnectorCatalogError,
    ConnectorCatalogService,
)
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
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair
from vibe.core.agent_loop import AgentLoop, AgentRuntimePolicy
from vibe.core.agents.manager import AgentManager
from vibe.core.config import (
    MCPHttp,
    MCPServer,
    MCPStaticAuth,
    MCPStdio,
    MCPStreamableHttp,
    MissingAPIKeyError,
    SessionLoggingConfig,
    VibeConfigSchema,
    build_default_orchestrator,
    resolve_api_key,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.experiments.cache import load_cached_eval_response
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import (
    ExperimentManager,
    config_variants_from_response,
)
from vibe.core.experiments.models import EvalResponse
from vibe.core.hooks.config import load_hooks_file, load_hooks_from_fs
from vibe.core.hooks.models import HookConfigResult
from vibe.core.paths import VIBE_HOME, WORKTREES_DIR
from vibe.core.session import last_session_pointer
from vibe.core.session.session_id import extract_suffix, generate_session_id
from vibe.core.session.session_index import warm_session_index
from vibe.core.session.session_interop import (
    InvalidLegacyInteropSourceError,
    export_legacy_committed_history,
    import_unified_committed_history,
    resolve_legacy_session_reference,
)
from vibe.core.session.session_lease import SessionLease
from vibe.core.session.session_loader import SessionLoader
from vibe.core.session.session_logger import SessionLogger
from vibe.core.skills.models import SkillInfo
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.telemetry.types import LaunchContext
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.models import ToolPermission
from vibe.core.tools.permissions import PermissionStore
from vibe.core.tracing import setup_tracing
from vibe.core.types import AgentStats, LLMMessage, Role, SessionMetadata
from vibe.core.utils import get_windows_bash_path, is_windows
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger, set_config_log_level
from vibe.utils import AgentEntrypoint
from vibe.utils.cache_store import FileSystemCacheStore
from vibe.utils.http import get_server_url_from_api_base

_SHORT_SESSION_ID_LENGTH = 8
type _CommandEnvironmentMode = Literal["unix", "git_bash", "powershell"]


def _command_environment_mode() -> _CommandEnvironmentMode:
    if not is_windows():
        return "unix"
    if get_windows_bash_path() is not None:
        return "git_bash"
    return "powershell"


def _build_unified_system_instructions(config: VibeConfigSchema) -> str:
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        build_vibe_code_system_instructions,
    )

    # The Vibe config layer resolves the GrowthBook system-prompt variant before
    # the experimental Runtime is composed. The SDK owns the corresponding text.
    return build_vibe_code_system_instructions(variant=config.system_prompt_id)


def _build_launch_context_from_services(
    services: SessionBackendServices,
) -> LaunchContext | None:
    """Read client metadata lazily after the app-server handshake."""
    try:
        client_info = services.client_info()
    except RuntimeError:
        return None
    return build_launch_context(
        agent_entrypoint=client_info.entrypoint,
        agent_version=__version__,
        client_name=client_info.name,
        client_version=client_info.version,
        terminal_emulator=client_info.terminal_emulator,
    )


if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustRuntimeBuiltinToolName,
    )

    from vibe.app_server._account import AccountGateway
    from vibe.app_server._identity import IdentityGateway
    from vibe.app_server._plugin_mcp import PluginMCPCatalog
    from vibe.app_server._plugins import SessionPlugins, UnifiedPluginProvider
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionContext
    from vibe.app_server.server import AppServer
    from vibe.core.tools.connectors.connector_registry import ConnectorRegistry
    from vibe.core.tools.mcp.registry import MCPRegistry


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
    experimental_harness: bool = field(default=False, kw_only=True)


class RuntimeSessionNotFoundError(RuntimeError):
    pass


class RuntimeAuthenticationError(RuntimeError):
    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"Authentication is required for provider: {provider}")


class RuntimeConfigurationError(RuntimeError):
    pass


class RuntimeUnfinishedMigrationError(RuntimeError):
    def __init__(self, session_id: str, source_backend: str) -> None:
        self.session_id = session_id
        self.source_backend = source_backend
        super().__init__(
            f"The {source_backend} session has unfinished recoverable work: "
            f"{session_id}"
        )


class RuntimeInvalidMigrationSourceError(RuntimeError):
    def __init__(self, session_id: str, source_backend: str, message: str) -> None:
        self.session_id = session_id
        self.source_backend = source_backend
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RootOpenRequest:
    options: SessionOptions
    client_info: ClientInfo
    session_id: str | None = None
    continue_latest: bool = False
    client_capabilities: ClientCapabilities = field(default_factory=ClientCapabilities)

    def __post_init__(self) -> None:
        if self.session_id is not None and self.continue_latest:
            raise ValueError("Cannot resume a session and continue the latest")


@dataclass(frozen=True, slots=True)
class _ImportedSession:
    session_id: str
    cwd: str | None
    root_session_id: str
    parent_session_id: str | None
    messages: list[LLMMessage]
    provenance: dict[str, JsonValue]
    active_model: str | None = None


@dataclass(frozen=True, slots=True)
class _AgentLoopBlueprint:
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    agent_name: str
    policy: AgentRuntimePolicy
    cwd: Path
    harness_files: HarnessFilesManager
    is_subagent: bool = False
    parent_session_id: str | None = None
    session_id: str | None = None
    session_dir: Path | None = None
    session_lease: SessionLease | None = None
    experiment_state: EvalResponse | None = None
    await_experiment_model: bool = False
    mcp_registry: MCPRegistry | None = None
    connector_registry: ConnectorRegistry | None = None

    def build(self) -> AgentLoop:
        return AgentLoop(
            config_orchestrator=self.config_orchestrator,
            agent_name=self.agent_name,
            max_turns=self.policy.max_turns,
            max_price=self.policy.max_price,
            max_tokens=self.policy.max_tokens,
            max_session_tokens=self.policy.max_session_tokens,
            enable_streaming=self.policy.enable_streaming,
            launch_context=self.policy.launch_context,
            is_subagent=self.is_subagent,
            defer_heavy_init=True,
            headless=self.policy.headless,
            hook_config_result=self.policy.hook_config_result,
            permission_store=self.policy.permission_store,
            mcp_registry=self.mcp_registry,
            connector_registry=self.connector_registry,
            cache_store=self.policy.cache_store,
            force_bypass_tool_permissions=self.policy.force_bypass_tool_permissions,
            local_managed_shell_runtime_enabled=(
                self.policy.local_managed_shell_runtime_enabled
            ),
            auto_title_enabled=self.policy.auto_title_enabled,
            experiment_state=self.experiment_state,
            await_experiment_model=self.await_experiment_model,
            parent_session_id=self.parent_session_id,
            cwd=self.cwd,
            harness_files=self.harness_files,
            session_id=self.session_id,
            session_dir=self.session_dir,
            session_lease=self.session_lease,
        )


@dataclass(frozen=True, slots=True)
class _SessionConfig:
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    harness_files: HarnessFilesManager


@dataclass(frozen=True, slots=True)
class _RootRuntimeBlueprint:
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    harness_files: HarnessFilesManager
    options: SessionOptions
    client_info: ClientInfo
    client_capabilities: ClientCapabilities
    hook_config_result: HookConfigResult
    cache_store: FileSystemCacheStore
    mcp_registry: MCPRegistry | None = None
    connector_registry: ConnectorRegistry | None = None

    @property
    def cwd(self) -> Path:
        return Path(self.options.cwd or Path.cwd()).expanduser().resolve()

    @property
    def config(self) -> VibeConfigSchema:
        return self.config_orchestrator.config

    def build(
        self,
        *,
        parent_session_id: str | None = None,
        session_id: str | None = None,
        session_dir: Path | None = None,
        session_lease: SessionLease | None = None,
    ) -> AgentLoop:
        policy = AgentRuntimePolicy(
            max_turns=self.options.max_turns,
            max_price=self.options.max_price,
            max_tokens=None,
            max_session_tokens=self.options.max_session_tokens,
            enable_streaming=True,
            launch_context=build_launch_context(
                agent_entrypoint=self.client_info.entrypoint,
                agent_version=__version__,
                client_name=self.client_info.name,
                client_version=self.client_info.version,
                terminal_emulator=self.client_info.terminal_emulator,
            ),
            headless=self.options.headless,
            hook_config_result=self.hook_config_result,
            permission_store=PermissionStore(),
            cache_store=self.cache_store,
            force_bypass_tool_permissions=self.options.auto_approve,
            local_managed_shell_runtime_enabled=(
                "terminal" not in self.client_capabilities.client_tools
            ),
            # The legacy AgentLoop generates background titles for CLI and Desktop.
            # Other clients retain the preview, and config can disable generation.
            auto_title_enabled=(
                self.client_info.entrypoint in {"cli", "desktop"}
                and self.config.session_logging.generate_titles
            ),
        )
        cached = load_cached_eval_response(self.config)
        return _AgentLoopBlueprint(
            config_orchestrator=self.config_orchestrator.copy(),
            agent_name=self.options.agent or self.config.default_agent,
            policy=policy,
            parent_session_id=parent_session_id,
            cwd=self.cwd,
            harness_files=self.harness_files,
            session_id=session_id,
            session_dir=session_dir,
            session_lease=session_lease,
            experiment_state=cached,
            await_experiment_model=cached is None and session_id is None,
            mcp_registry=self.mcp_registry,
            connector_registry=self.connector_registry,
        ).build()


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


class AgentRuntimeFactory:
    def resolve_latest(self, source: AgentLoop, cwd: Path) -> str:
        _require_session_logging(source.config)
        return _find_session_to_continue(source.config, cwd=cwd)

    async def resume_root(self, source: AgentLoop, session_id: str) -> None:
        """Resume a stored session by rebinding the existing loop in place.

        The existing MCP connections, tool registry, git context, and config
        are all reused — only session-scoped state (ID, messages, stats,
        session logger) is swapped. This avoids the cold-rebuild overhead of
        creating a fresh AgentLoop on every resume.

        The rebind runs before waiting for deferred init so the UI can render
        the resumed transcript immediately. ``finish_resume_root`` must be
        called afterward to await readiness and hydrate experiments.
        """
        session_id = await asyncio.to_thread(
            _resolve_resume_session_id, source.config, session_id
        )
        lease = await asyncio.to_thread(
            _acquire_session_lease, source.config, session_id
        )
        previous_model = source.config.get_active_model().alias
        previous_session_pinned = source.session_logger.active_model is not None
        try:
            try:
                session_path, loaded_messages, metadata = await asyncio.to_thread(
                    _load_session, source.config, session_id
                )
                active_model = config_active_model(metadata)
            except RuntimeSessionNotFoundError:
                imported = await asyncio.to_thread(
                    _load_unified_import, source.config, session_id
                )
                if imported is None:
                    raise
                active_model = imported.active_model
                await _restore_session_active_model(
                    source.config_orchestrator,
                    active_model,
                    clear_existing=previous_session_pinned,
                )
                logger = SessionLogger(
                    source.config.session_logging,
                    session_id,
                    cwd=Path(imported.cwd) if imported.cwd is not None else source.cwd,
                )
                if logger.session_metadata is None or logger.session_dir is None:
                    raise RuntimeConfigurationError(
                        "Legacy session logging must be enabled for import"
                    )
                logger.session_metadata.import_provenance = imported.provenance
                logger.session_metadata.parent_session_id = imported.parent_session_id
                await logger.save_interaction(
                    imported.messages,
                    AgentStats(),
                    source.config,
                    source.tool_manager,
                    source.agent_profile,
                    allow_empty=True,
                )
                session_path = logger.session_dir
                loaded_messages, metadata = await asyncio.to_thread(
                    SessionLoader.load_session, session_path
                )
            else:
                await _restore_session_active_model(
                    source.config_orchestrator,
                    active_model,
                    clear_existing=previous_session_pinned,
                )
            # ``_load_session`` already parsed metadata.json into ``metadata``;
            # parse that dict instead of re-reading the file from disk.
            session_metadata = SessionMetadata.model_validate(metadata)
            stats = _build_stats(source, metadata)
            # Rebind before waiting for deferred init so the UI can render the
            # resumed transcript immediately. The init thread's
            # ``update_system_prompt`` inserts at position 0 (see
            # ``MessageList.update_system_prompt``), so it lands correctly on top
            # of the resumed messages whenever it completes — same pattern as
            # ``resume_blueprint``.
            source.rebind_to_session(
                session_id,
                session_path,
                loaded_messages,
                session_metadata=session_metadata,
                parent_session_id=_parent_session_id(metadata),
                stats=stats,
            )
            if source.config.get_active_model().alias != previous_model:
                await source.reload_with_initial_messages()
        except BaseException:
            if lease is not None:
                await asyncio.to_thread(lease.release)
            raise
        source.replace_session_lease(lease)

    async def finish_resume_root(self, source: AgentLoop, session_id: str) -> None:
        """Finish a resume: await deferred init, then hydrate experiments.

        Called after the ``session/resume`` RPC response is sent so the client
        can render the transcript while MCP/connector init completes in the
        background. Both steps are best-effort: the rebind already committed, so
        a deferred-init or hydration failure must not abort the caller before it
        emits ``runtime/updated`` — the degraded state (e.g. MCP discovery
        errors) is carried in the runtime snapshot instead.

        Init-duration recording lives in ``wait_until_ready`` via
        ``_ensure_init_duration_recorded``, not here.
        """
        try:
            await source._await_deferred_init()
        except Exception:
            logger.exception(
                "Deferred init failed after resuming session_id=%s", session_id
            )
        try:
            await source.hydrate_experiments_from_session()
        except Exception:
            logger.exception(
                "Failed to hydrate experiments after resuming session_id=%s", session_id
            )

    async def resume_blueprint(
        self,
        blueprint: _RootRuntimeBlueprint,
        session_id: str,
        session_lease: SessionLease | None = None,
    ) -> AgentLoop:
        try:
            session_path, loaded_messages, metadata = await asyncio.to_thread(
                _load_session, blueprint.config, session_id
            )
        except RuntimeSessionNotFoundError:
            imported = await asyncio.to_thread(
                _load_unified_import, blueprint.config, session_id
            )
            if imported is None:
                raise
            await _restore_session_active_model(
                blueprint.config_orchestrator, imported.active_model
            )
            replacement = blueprint.build(
                parent_session_id=imported.parent_session_id,
                session_id=session_id,
                session_lease=session_lease,
            )
            try:
                replacement.messages.reset_preserving_system(imported.messages)
                session_metadata = replacement.session_logger.session_metadata
                if session_metadata is None:
                    raise RuntimeConfigurationError(
                        "Legacy session logging must be enabled for import"
                    )
                session_metadata.import_provenance = imported.provenance
                session_metadata.environment["working_directory"] = imported.cwd or str(
                    blueprint.cwd
                )
                await replacement.session_logger.save_interaction(
                    replacement.messages,
                    AgentStats(),
                    replacement.config,
                    replacement.tool_manager,
                    replacement.agent_profile,
                    allow_empty=True,
                )
                await replacement.hydrate_experiments_from_session(refresh_prompt=False)
            except BaseException:
                await close_agent_loop(replacement)
                raise
            return replacement
        await _restore_session_active_model(
            blueprint.config_orchestrator, config_active_model(metadata)
        )
        replacement = blueprint.build(
            parent_session_id=_parent_session_id(metadata),
            session_id=session_id,
            session_dir=session_path,
            session_lease=session_lease,
        )
        # Set messages and stats immediately so the UI can render the stored
        # transcript while the runtime (git, MCP) warms up in the background.
        # MessageList.update_system_prompt() inserts at position 0 when the
        # background thread eventually sets the system prompt, so no system
        # message needs to be present here.
        try:
            replacement.messages.reset_preserving_system(loaded_messages)
            _apply_stored_stats(replacement, metadata)
            # refresh_prompt=False: deferred init hasn't completed yet (git, MCP),
            # so refresh_system_prompt() — gated by @requires_init — would block.
            # The background thread updates the system prompt once init finishes,
            # same as a fresh session start.
            await replacement.hydrate_experiments_from_session(refresh_prompt=False)
        except BaseException:
            await close_agent_loop(replacement)
            raise
        return replacement

    async def create_child(
        self,
        parent: AgentLoop,
        agent_name: str,
        *,
        session_id: str | None = None,
        session_dir: Path | None = None,
    ) -> AgentLoop:
        parent_session_dir = parent.session_logger.session_dir
        orchestrator = parent.config_orchestrator.copy()
        session_logging = SessionLoggingConfig(
            save_dir=(
                str(parent_session_dir / "agents")
                if parent_session_dir is not None
                else ""
            ),
            session_prefix=agent_name,
            enabled=parent_session_dir is not None,
        )
        failures = await orchestrator.set_field(
            "/session_logging",
            session_logging.model_dump(mode="json"),
            reason="configure child session logging",
            target_layer=OverridesLayer.NAME,
        )
        if failures:
            raise RuntimeConfigurationError(
                "Failed to configure child session logging"
            ) from failures[0]
        child_session_id = session_id or generate_session_id()
        lease = await asyncio.to_thread(
            _acquire_session_lease, parent.config, child_session_id
        )
        try:
            return self._create_like(
                parent,
                config_orchestrator=orchestrator,
                agent_name=agent_name,
                is_subagent=True,
                parent_session_id=parent.session_id,
                session_id=child_session_id,
                session_dir=session_dir,
                session_lease=lease,
                share_permissions=True,
            )
        except BaseException:
            if lease is not None:
                await asyncio.to_thread(lease.release)
            raise

    async def resume_child(
        self, parent: AgentLoop, agent_name: str, session_id: str, session_dir: Path
    ) -> AgentLoop:
        loaded_messages, metadata = await asyncio.to_thread(
            SessionLoader.load_session, session_dir
        )
        child = await self.create_child(
            parent, agent_name, session_id=session_id, session_dir=session_dir
        )
        # Eager message setting: background thread inserts system prompt at position 0
        # when _complete_init finishes, same pattern as resume_blueprint.
        try:
            child.messages.reset_preserving_system(loaded_messages)
            _apply_stored_stats(child, metadata)
            # refresh_prompt=False for the same reason as resume_blueprint: the child's
            # deferred init hasn't run yet, so @requires_init would block here.
            await child.hydrate_experiments_from_session(refresh_prompt=False)
        except BaseException:
            await close_agent_loop(child)
            raise
        return child

    async def fork(self, source: AgentLoop, message_id: str | None) -> AgentLoop:
        session_id = generate_session_id(suffix=extract_suffix(source.session_id))
        lease = await asyncio.to_thread(
            _acquire_session_lease, source.config, session_id
        )
        forked: AgentLoop | None = None
        try:
            forked = self._create_like(
                source,
                agent_name=source.agent_profile.name,
                parent_session_id=source.session_id,
                session_id=session_id,
                session_lease=lease,
            )
            await forked.wait_until_ready()
            forked.messages.extend(_messages_for_fork(source, message_id))
            await forked.session_logger.save_interaction(
                forked.messages,
                forked.stats,
                forked.config,
                forked.tool_manager,
                forked.agent_profile,
            )
        except BaseException:
            if forked is not None:
                await close_agent_loop(forked)
            elif lease is not None:
                await asyncio.to_thread(lease.release)
            raise
        return forked

    @staticmethod
    def _create_like(
        source: AgentLoop,
        *,
        config_orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None,
        agent_name: str,
        is_subagent: bool = False,
        parent_session_id: str | None = None,
        session_id: str | None = None,
        session_dir: Path | None = None,
        session_lease: SessionLease | None = None,
        share_permissions: bool = False,
    ) -> AgentLoop:
        policy = source.runtime_policy
        if not share_permissions:
            policy = replace(policy, permission_store=PermissionStore())
        if is_subagent:
            policy = replace(policy, enable_streaming=False)
        replacement = _AgentLoopBlueprint(
            config_orchestrator=(
                config_orchestrator or source.config_orchestrator.copy()
            ),
            agent_name=agent_name,
            policy=policy,
            is_subagent=is_subagent,
            parent_session_id=parent_session_id,
            cwd=source.cwd,
            harness_files=source.harness_files,
            session_id=session_id,
            session_dir=session_dir,
            session_lease=session_lease,
            experiment_state=source.experiment_manager.export_state(),
            mcp_registry=(
                source.mcp_registry.clone_configuration()
                if source.mcp_registry is not None
                else None
            ),
            connector_registry=(
                source.connector_registry.clone_configuration()
                if source.connector_registry is not None
                else None
            ),
        ).build()
        return replacement


class HarnessProcess:
    def __init__(
        self,
        harness_files: HarnessFilesManager | None = None,
        *,
        experimental_harness: bool = False,
    ) -> None:
        from vibe.app_server._mcp_auth import MCPAuthenticationService
        from vibe.app_server.mcp_catalog import MCPCatalogService
        from vibe.app_server.plugin_catalog import PluginCatalogService

        self.runtime_factory = AgentRuntimeFactory()
        self.cache_store = FileSystemCacheStore()
        self.harness_files = harness_files or HarnessFilesManager(
            sources=("user", "project")
        )
        self._configuration_lock = threading.Lock()
        self._configured = False
        self._staged_roots: dict[str, AgentLoop] = {}
        self._staged_roots_lock = asyncio.Lock()
        self._closed = False
        self._experimental_harness_host: object | None = None
        startup_issue: ConfigIssue | None = None
        if experimental_harness:
            try:
                self._experimental_harness_host = create_experimental_harness_host()
            except (ExperimentalHarnessUnavailableError, ImportError) as exc:
                startup_issue = ConfigIssue(
                    file="--experimental-harness",
                    message=f"{exc}; falling back to the legacy harness.",
                )
        self.host_handler = HostRequestHandler(
            self.harness_files, startup_issue=startup_issue
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

    def create_session_backend_host(
        self, services: SessionBackendServices
    ) -> SessionBackendHost:
        host = self._experimental_harness_host
        if host is not None:
            from vibe.app_server._unified_harness_backend_adapter import (
                adapt_harness_host,
            )

            return adapt_harness_host(
                host,
                self.build_unified_session_context,
                services,
                launch_context_getter=lambda: _build_launch_context_from_services(
                    services
                ),
            )

        from vibe.app_server._legacy_session_runtime import (
            create_legacy_session_backend_host,
        )

        return create_legacy_session_backend_host(
            open_root=self.open_root,
            runtime_factory=self.runtime_factory,
            host_handler=self.host_handler,
            stage_root=self.stage_root,
            services=services,
            mcp_catalog_service=self.mcp_catalog,
            connector_catalog_service=self.connector_catalog,
            account_gateway=services.account_gateway(),
            identity_gateway=services.identity_gateway(),
        )

    async def stage_root(self, root: AgentLoop) -> None:
        superseded: AgentLoop | None = None
        async with self._staged_roots_lock:
            if self._closed:
                superseded = root
            else:
                superseded = self._staged_roots.get(root.session_id)
                self._staged_roots[root.session_id] = root
        if superseded is not None and superseded is not root:
            await close_agent_loop(superseded)
        if self._closed:
            if superseded is root:
                await close_agent_loop(root)
            raise RuntimeError("The app-server harness process is closed")

    async def close(self) -> None:
        async with self._staged_roots_lock:
            if self._closed:
                return
            self._closed = True
            staged = list(self._staged_roots.values())
            self._staged_roots.clear()
        errors: list[BaseException] = []
        for root in staged:
            try:
                await close_agent_loop(root)
            except BaseException as exc:
                errors.append(exc)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to close staged session runtimes", errors)

    async def build_session_runtime(self, options: SessionOptions) -> RuntimeSnapshot:
        session_config = await self._build_session_config(options)
        return build_runtime_snapshot(
            options, session_config.config_orchestrator, session_config.harness_files
        )

    async def build_sessionless_mcp_catalog(
        self,
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        session_config = await self._build_session_config(SessionOptions())
        return session_config.config_orchestrator

    async def build_unified_session_context(  # noqa: PLR0914, PLR0915 - composition root
        self,
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: AgentEntrypoint = "cli",
    ) -> UnifiedSessionContext:
        from mistralai_vibe_local_harness import (  # pyright: ignore[reportMissingImports]
            HarnessSession,
        )
        from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
            RustAutomaticCompactionPolicy,
            RustContextSettings,
            RustDisabledCompactionPolicy,
            RustEnabledRuntimeToolFeature,
            RustFilesystemLargeOutputPolicy,
            RustGitBashCommandEnvironment,
            RustHarnessCapabilitySet,
            RustHarnessConfig,
            RustHarnessSettings,
            RustPowerShellCommandEnvironment,
            RustProgrammaticToolSettings,
            RustProvidedToolDefinition,
            RustToolGroupDefinition,
            RustToolSettings,
            RustTurnSettings,
            RustUnixCommandEnvironment,
        )
        from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
            LegacyImportSource,
            LegacySessionReference as HarnessLegacySessionReference,
            LocalModelRoute,
            LocalRuntimeAdapterConfig,
            compile_foreign_hooks,
            tool_catalog_for_config,
        )

        from vibe.app_server._plugins import (
            core_plugins,
            plugin_issues,
            requested_plugin_definitions,
        )

        # Imported here rather than at module scope: it depends on the Harness
        # package, which only exists under the experimental-harness extra.
        from vibe.app_server._provider_credentials import ProviderCredentialService
        from vibe.app_server._unified_harness_backend_adapter import (
            CorrelationIdHolder,
            RequestSentQueue,
            UnifiedRuntimeDerivation,
            UnifiedSessionContext,
            UnifiedSessionSettings,
        )
        from vibe.app_server._unified_permissions import UnifiedPermissionResolver
        from vibe.core.llm.utility_completion import (
            is_fast_utility_model,
            select_utility_model,
        )
        from vibe.questions import UserQuestionRequest, UserQuestionResult

        session_config = await self._build_session_config(options)
        config_orchestrator = session_config.config_orchestrator
        harness_files = session_config.harness_files
        config = config_orchestrator.config
        cwd = Path(options.cwd or Path.cwd()).expanduser().resolve()
        workspace_roots = tuple(
            Path(root).expanduser().resolve() for root in options.workspace_roots
        ) or (cwd,)
        plugins, plugin_provider, plugin_mcp = await self._build_plugins(
            session_config, cwd
        )
        command_environment_mode = _command_environment_mode()
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
        credentials = ProviderCredentialService(config_orchestrator)

        # One holder per session, shared by every derivation's adapter config and
        # the context: the Mistral adapter writes the provider correlation id into
        # it, and telemetry forwarding reads it.
        correlation = CorrelationIdHolder()

        # Same lifetime and sharing as ``correlation``: the completion adapter
        # appends each request's shape here, the adapter drains it to telemetry.
        request_sent = RequestSentQueue()

        # Both the credential service and the agent manager read the
        # orchestrator lazily, so they stay correct across every config
        # mutation and must outlive any single derivation.
        agents = AgentManager(
            config_orchestrator,
            options.agent or config.default_agent,
            harness_files=harness_files,
        )
        if require_api_key:
            config_orchestrator.config.require_active_provider_api_key()
        # The store is the session's memory of what the user approved
        permissions = PermissionStore()
        tools = ToolManager(
            lambda: config_orchestrator.config,
            defer_mcp=True,
            cwd=cwd,
            harness_files=harness_files,
            permission_getter=permissions.get_tool_permission,
        )
        permission_resolver = UnifiedPermissionResolver(
            tools, permissions, config_orchestrator
        )

        def resolve_legacy_source(
            session_id: str,
        ) -> HarnessLegacySessionReference | None:
            reference = resolve_legacy_session_reference(
                session_id, config_orchestrator.config.session_logging
            )
            if reference is None:
                return None
            return HarnessLegacySessionReference(
                session_id=reference.session_id, cwd=reference.cwd
            )

        def load_legacy_source(session_id: str) -> LegacyImportSource:
            try:
                export = export_legacy_committed_history(
                    session_id, config_orchestrator.config.session_logging
                )
            except InvalidLegacyInteropSourceError as exc:
                return LegacyImportSource(state="invalid", error=str(exc))
            if export is None:
                return LegacyImportSource(state="absent")
            return LegacyImportSource(
                state="quiescent",
                reference=HarnessLegacySessionReference(
                    session_id=export.reference.session_id, cwd=export.reference.cwd
                ),
                store_revision=export.store_revision,
                history=export.history,
                active_model=export.active_model,
            )

        # Discover the user's hooks once. The raw parse -- including any parse/duplicate
        # diagnostics on result.issues -- is settings-independent, so load it before the
        # derivation closure and surface those diagnostics as session notices, matching
        # legacy (which projects hook_config_issues) and the design's failure table.
        hook_result = await asyncio.to_thread(
            load_hooks_from_fs, harness_files=session_config.harness_files
        )
        mcp_catalog = await self.mcp_catalog.resolve_catalog(config_orchestrator)
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

        def derive_config(
            config: VibeConfigSchema, settings: UnifiedSessionSettings
        ) -> UnifiedRuntimeDerivation:
            active_model = config.get_active_model()
            compaction_model = config.get_compaction_model()
            title_model_config, _title_provider = select_utility_model(config)
            title_model = LocalModelRoute(
                model=title_model_config.name, temperature=0.0, thinking="off"
            )
            title_model_is_fast = is_fast_utility_model(config)
            # Match the legacy policy: only interactive terminal/desktop clients
            # get background titles; other clients keep the message preview.
            auto_title_enabled = (
                config.session_logging.generate_titles
                and entrypoint in {"cli", "desktop"}
            )
            compaction_policy = (
                RustAutomaticCompactionPolicy(
                    token_threshold=active_model.auto_compact_threshold
                )
                if active_model.auto_compact_threshold > 0
                else RustDisabledCompactionPolicy()
            )
            provider = config.get_provider_for_model(active_model)
            derived_tools = ToolManager(
                lambda: config, defer_mcp=True, cwd=cwd, harness_files=harness_files
            )
            available_tools = set(derived_tools.available_tools)
            bypass_approval = options.auto_approve or config.bypass_tool_permissions
            max_iterations = settings.max_turns or options.max_turns or 1_000
            skill_issues, skills = discover_session_skills(
                lambda: config_orchestrator.config,
                harness_files=harness_files,
                plugin_skills=plugins.materialized.resolution.skills,
                plugin_contexts=core_plugins(plugins),
                skill_tool_available="skill" in available_tools,
            )
            return UnifiedRuntimeDerivation(
                runtime=build_unified_runtime_snapshot(
                    config_orchestrator,
                    agents,
                    issues=[
                        *plugin_issues(plugins),
                        *skill_issues,
                        *_hook_config_issues(hook_result),
                    ],
                    skills=skills.catalogue,
                    tools=available_tools,
                    custom_tool_names=frozenset(derived_tools.custom_tool_names),
                    hooks_count=len(hook_result.hooks),
                    auto_approve=options.auto_approve,
                ),
                core_config=RustHarnessConfig(
                    task_id="runtime-template",
                    system_instructions=_build_unified_system_instructions(config),
                    settings=RustHarnessSettings(
                        turn=RustTurnSettings(max_iterations=max_iterations),
                        context=RustContextSettings(compaction=compaction_policy),
                        tools=RustToolSettings(
                            programmatic=RustProgrammaticToolSettings(
                                max_effects=128, max_operations=1024
                            ),
                            subagents=RustEnabledRuntimeToolFeature(),
                            background_processes=RustEnabledRuntimeToolFeature(),
                            command_environment=command_environment,
                            large_output=RustFilesystemLargeOutputPolicy(),
                        ),
                    ),
                    capabilities=RustHarnessCapabilitySet(
                        tool_groups=(
                            [
                                RustToolGroupDefinition(
                                    name="ui",
                                    description="Interactive user interface tools",
                                    tools=[
                                        RustProvidedToolDefinition(
                                            name="ask_user_question",
                                            description=(
                                                "Ask the user one or more questions and wait "
                                                "for their answers."
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
                        skills=list(skills.definitions),
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
                        thinking=active_model.thinking,
                    ),
                    compaction_model=LocalModelRoute(
                        model=compaction_model.name,
                        temperature=compaction_model.temperature,
                        thinking=compaction_model.thinking,
                    ),
                    title_model=title_model if auto_title_enabled else None,
                    title_model_is_fast=title_model_is_fast,
                    max_tokens=settings.max_tokens,
                    thinking=active_model.thinking,
                    reasoning_field_name=provider.reasoning_field_name,
                    emits_finish_reason=provider.emits_finish_reason,
                    extra_headers=dict(provider.extra_headers),
                    project_id=provider.project_id,
                    region=provider.region,
                    timeout_s=config.api_timeout,
                    retry_max_elapsed_time_s=config.api_retry_max_elapsed_time,
                    cwd=cwd,
                    workspace_roots=workspace_roots,
                    # The library environment goes on top: a plugin that declares a
                    # Python or Node library is only usable if the search paths
                    # materialization computed reach the process that runs a
                    # command. Materialization already folded the inherited
                    # PYTHONPATH and NODE_PATH into those values, so overriding is
                    # prepending, not discarding.
                    env={**os.environ, **plugins.materialized.process_environment},
                    command_environment=command_environment_mode,
                    shell=getattr(
                        derived_tools.get_tool_config(
                            "bash"
                            if command_environment_mode == "unix"
                            else command_environment_mode
                        ),
                        "shell",
                        None,
                    ),
                    process_authority="host_shell",
                    bypass_approval=bypass_approval,
                    tool_modes=_rust_tool_modes(
                        available_tools,
                        lambda name: derived_tools.get_tool_config(name).permission,
                        bypass_approval=bypass_approval,
                    ),
                    permission_resolver=permission_resolver.resolve,
                    skills=skills.payloads,
                    correlation_id_sink=correlation.record,
                    request_sent_sink=request_sent.record,
                ),
            )

        def derive(settings: UnifiedSessionSettings) -> UnifiedRuntimeDerivation:
            return derive_config(config_orchestrator.config, settings)

        async def preflight(
            candidate: VibeConfigSchema, settings: UnifiedSessionSettings
        ) -> None:
            derivation = await asyncio.to_thread(derive_config, candidate, settings)

            def validate_core_config() -> None:
                core = HarnessSession.create(
                    derivation.core_config.model_dump_json(exclude_none=True), "[]"
                )
                core.close()

            await asyncio.to_thread(validate_core_config)

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
                hook_result, harness_files=harness_files, cwd=cwd
            ),
            tool_catalog=lambda: tool_catalog_for_config(core_config_json),
        )

        return UnifiedSessionContext(
            storage_root=config.session_logging.save_dir,
            legacy_source_loader=load_legacy_source,
            legacy_source_resolver=resolve_legacy_source,
            plugins=plugins,
            plugin_provider=plugin_provider,
            plugin_mcp=plugin_mcp,
            requested_plugins=tuple(requested_plugin_definitions(plugins)),
            config_orchestrator=config_orchestrator,
            harness_files=harness_files,
            agents=agents,
            derive=derive,
            permissions=permission_resolver,
            hooks=hooks,
            mcp_catalog=mcp_catalog,
            mcp_authorization_provider=self.mcp_authentication,
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
        )

    async def _build_session_config(self, options: SessionOptions) -> _SessionConfig:
        cwd = Path(options.cwd or Path.cwd()).expanduser().resolve()
        workspace_roots = [
            Path(root).expanduser().resolve() for root in options.workspace_roots
        ]
        harness_files = self.harness_files.for_session(
            cwd, workspace_roots=workspace_roots
        )
        if options.trust_workspace:
            harness_files.trust_store.trust_for_session(cwd)
        overrides = _session_config_overrides(options)
        config_orchestrator = await build_default_orchestrator(
            overrides, harness_files=harness_files
        )
        await _apply_cached_experiment_variants(config_orchestrator)
        return _SessionConfig(
            config_orchestrator=config_orchestrator, harness_files=harness_files
        )

    async def build_root_blueprint(
        self,
        options: SessionOptions,
        client_info: ClientInfo,
        client_capabilities: ClientCapabilities | None = None,
    ) -> _RootRuntimeBlueprint:
        session_config = await self._build_session_config(options)
        config_orchestrator = session_config.config_orchestrator
        harness_files = session_config.harness_files
        hook_config_result = await asyncio.to_thread(
            load_hooks_from_fs, harness_files=harness_files
        )
        await asyncio.to_thread(self._configure_process, config_orchestrator.config)
        return _RootRuntimeBlueprint(
            config_orchestrator=config_orchestrator,
            harness_files=harness_files,
            options=options,
            client_info=client_info,
            client_capabilities=client_capabilities or ClientCapabilities(),
            hook_config_result=hook_config_result,
            cache_store=self.cache_store,
            mcp_registry=(
                None
                if self._experimental_harness_host is not None
                else await self._build_legacy_mcp_registry(config_orchestrator)
            ),
            connector_registry=(
                None
                if self._experimental_harness_host is not None
                else await self._build_connector_registry(config_orchestrator)
            ),
        )

    async def open_root(self, request: RootOpenRequest) -> AgentLoop:
        if self._experimental_harness_host is not None:
            raise RuntimeConfigurationError(
                "The Unified Harness backend owns its sessions and never opens a "
                "legacy runtime."
            )
        try:
            if request.session_id is not None:
                staged = await self._claim_staged_root(request.session_id)
                if staged is not None:
                    return staged
            blueprint = await self.build_root_blueprint(
                request.options, request.client_info, request.client_capabilities
            )
            session_id = request.session_id
            if request.continue_latest:
                session_id = _find_session_to_continue(
                    blueprint.config, cwd=blueprint.cwd
                )
            if session_id is not None:
                session_id = await asyncio.to_thread(
                    _resolve_resume_session_id, blueprint.config, session_id
                )
                lease = await asyncio.to_thread(
                    _acquire_session_lease, blueprint.config, session_id
                )
                try:
                    return await self.runtime_factory.resume_blueprint(
                        blueprint, session_id, lease
                    )
                except BaseException:
                    if lease is not None:
                        await asyncio.to_thread(lease.release)
                    raise
            session_id = generate_session_id()
            lease = await asyncio.to_thread(
                _acquire_session_lease, blueprint.config, session_id
            )
            try:
                return blueprint.build(session_id=session_id, session_lease=lease)
            except BaseException:
                if lease is not None:
                    await asyncio.to_thread(lease.release)
                raise
        except MissingAPIKeyError as exc:
            raise RuntimeAuthenticationError(exc.provider_name) from exc
        except (ValidationError, ValueError) as exc:
            raise RuntimeConfigurationError(str(exc)) from exc

    async def _build_legacy_mcp_registry(
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema]
    ) -> MCPRegistry:
        from vibe.app_server._legacy_session_backend import (
            configure_legacy_mcp_registry,
        )
        from vibe.core.tools.mcp.registry import MCPRegistry

        # Bound in the registry's name, not the orchestrator's: this is the
        # blueprint's orchestrator and the session's loop is handed a copy of
        # it, so nothing holds this one once the blueprint has been consumed,
        # while the registry it arms here goes on resolving through the
        # binding. The registry is how long that binding is needed for.
        registry = MCPRegistry()
        configuration = await self.mcp_catalog.resolve_catalog(
            orchestrator, owner=registry
        )
        cache_root = (
            Path(orchestrator.config.session_logging.save_dir)
            .expanduser()
            .resolve()
            .parent
            / "mcp-descriptors"
            / "legacy"
        )
        configure_legacy_mcp_registry(
            registry,
            configuration,
            self.mcp_authentication,
            descriptor_cache_root=cache_root,
        )
        return registry

    async def _build_plugins(
        self, session_config: _SessionConfig, cwd: Path
    ) -> tuple[SessionPlugins, UnifiedPluginProvider, PluginMCPCatalog]:
        """The Host's own resolve, and the provider the Runtime binds through.

        One call, because both need the same MCP catalog and connector registry
        and building them twice would open two MCP client pools for one session.
        """
        from vibe.app_server._plugins import (
            AgentToolCatalogue,
            UnifiedPluginProvider,
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
                config_orchestrator=session_config.config_orchestrator,
                plugin_mcp=plugin_mcp,
                connector_registry=connector_registry,
                # Kept so ``plugin/reload`` repeats this resolve. A rescan that
                # walked elsewhere would report the session's plugins uninstalled.
                harness_files=session_config.harness_files,
                agent_tools=agent_tools,
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
        try:
            catalog = await self.connector_catalog.resolve_catalog(orchestrator)
        except ConnectorCatalogError:
            logger.warning("Connector catalog is unavailable during session startup")
            catalog = None
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

    async def _claim_staged_root(self, session_id: str) -> AgentLoop | None:
        async with self._staged_roots_lock:
            if self._closed:
                raise RuntimeError("The app-server harness process is closed")
            return self._staged_roots.pop(session_id, None)

    def _configure_process(self, config: VibeConfigSchema) -> None:
        with self._configuration_lock:
            if self._configured:
                return
            setup_tracing(config)
            warm_session_index(config.session_logging)
            set_config_log_level(config.log_level)
            self._configured = True


async def create_harness_server(
    transport: JsonRpcTransport,
    *,
    transport_kind: TransportKind,
    process: HarnessProcess | None = None,
    experimental_harness: bool = False,
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

    if process is not None and experimental_harness:
        raise ValueError(
            "experimental_harness cannot be combined with an existing HarnessProcess"
        )
    process = process or HarnessProcess(experimental_harness=experimental_harness)
    return HarnessServer(
        _server=AppServer(
            transport,
            transport_kind=transport_kind,
            host_handler=process.host_handler,
            session_backend_host_factory=process.create_session_backend_host,
            mcp_catalog_service=process.mcp_catalog,
            connector_catalog_service=process.connector_catalog,
            plugin_catalog_service=process.plugin_catalog,
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
    result: HookConfigResult, *, harness_files: HarnessFilesManager, cwd: Path
) -> list[Any]:
    """Map discovered project/user hook declarations to harness hook definitions.

    Consumes ``result.hooks`` (the parsed ``[[hooks]]`` from ``hooks.toml``), the
    same field the legacy loop uses -- not ``result.runtime_hooks``, which only the
    plugin loader populates.

    ``source`` scopes the binding id (``f"{source}:{name}"``). A user hook is labelled
    ``"user"`` and a project hook the session cwd, so a persisted project binding cannot be
    matched by a same-named user hook on a resume where project trust has since been lost.
    """
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ForeignHookDefinition,
    )

    user_names = _user_hook_names(harness_files)
    cwd_source = str(cwd)

    return [
        ForeignHookDefinition(
            name=hook.name,
            point=hook.type.value,
            command=hook.command,
            source="user" if hook.name in user_names else cwd_source,
            match=hook.match,
            order=index,
            timeout_s=hook.timeout if hook.timeout is not None else 60.0,
            strict=hook.strict,
        )
        for index, hook in enumerate(result.hooks)
    ]


def build_runtime_snapshot(
    options: SessionOptions,
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema],
    harness_files: HarnessFilesManager,
    *,
    issues: Sequence[ConfigIssue] = (),
) -> RuntimeSnapshot:
    config = config_orchestrator.config
    agents = AgentManager(
        config_orchestrator,
        options.agent or config.default_agent,
        harness_files=harness_files,
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
    custom_tool_names: frozenset[str] = frozenset(),
    hooks_count: int = 0,
    auto_approve: bool = False,
) -> RuntimeSnapshot:
    """Project the layered config into the runtime state the client observes.

    The tools are the resolved Vibe catalogue used to derive the local Runtime's
    executable tool modes. ``skills`` and ``issues`` are supplied by the caller
    because they come from discovery and plugin resolution, not from the layered
    config. ``hooks_count`` is supplied by the caller because hook discovery
    happens outside the layered config. Integration projections are filled by
    their owning adapters.
    """
    config = config_orchestrator.config
    active_model = config.get_active_model()
    active, available = project_agent_summaries(
        agents.active_profile, agents.available_agents.values()
    )
    return RuntimeSnapshot(
        config=project_config_view(
            config, active_model_pinned=active_model_is_pinned(config_orchestrator)
        ),
        active_agent=active,
        agents=available,
        skills=project_skill_summaries(skills),
        tools=[
            ToolSummary(name=name, is_custom=name in custom_tool_names)
            for name in tools
        ],
        stats=AgentStatsSnapshot(
            input_price_per_million=active_model.input_price,
            output_price_per_million=active_model.output_price,
            cached_input_price_per_million=active_model.cached_input_price,
        ),
        context_window=active_model.auto_compact_threshold,
        issues=list(issues),
        hooks_count=hooks_count,
        connectors=ConnectorCounts(),
        mcp=MCPState(),
        bypass_tool_permissions=auto_approve or config.bypass_tool_permissions,
        experimental_harness=True,
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


def _rust_tool_modes(
    available_tools: set[str],
    permission_of: Callable[[str], ToolPermission],
    *,
    bypass_approval: bool,
) -> dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny"]]:
    """Map the effective Vibe tool catalogue onto Rust builtin approval modes.

    A builtin is denied when the catalogue offers no tool it stands for, so a
    ``config/write`` that removes every shell tool also stops the Runtime from
    running commands — but a platform that merely spells the shell differently
    does not.

    A configured ``always`` comes out as ``ask``. The mode is the only thing the
    Runtime consults before it runs a builtin, and ``allow`` retires the
    resolver — and with it every rule a Vibe tool applies to the *call*: the
    prompt on ``**/.env`` and friends, the path denylist, the command denylist.
    ``AgentLoop._should_execute_tool`` runs ``resolve_permission`` before it ever
    reads the configured permission, so a tool-wide ``always`` never bought "skip
    the tool's own rules" on the legacy backend either. ``ask`` hands the call to
    the resolver, which answers ``allow`` for anything the tool clears — the
    prompts that come back are the ones legacy was already raising.
    """
    from vibe.app_server._unified_permissions import RUST_BUILTIN_TOOL_SOURCES

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
            configured[builtin] = _RUST_MODE_BY_PERMISSION[strictest]

    modes: dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny"]] = {
        builtin: "ask" if mode == "allow" and not bypass_approval else mode
        for builtin, mode in configured.items()
    }
    # Not downgraded: no Vibe tool stands behind `process.start`, so the resolver
    # has no rule to apply to it and would answer with a bare ask that `grant`
    # cannot record -- a prompt on every background start, for nothing. It
    # follows the shell's configured mode, as it always has.
    modes["process.start"] = configured["file_system.bash"]
    for builtin in ("process.output", "process.write", "process.list", "process.stop"):
        modes[builtin] = "allow"
    return modes


def rust_agent_tool_ceiling(
    available_tools: set[str],
    permission_of: Callable[[str], ToolPermission],
    overrides: Mapping[str, Any],
) -> dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny"]]:
    # A ceiling, never a grant: the Runtime keeps the stricter of this and the mode
    # the parent holds, so the worst a wrong answer here does is deny a child
    # something it was allowed. ``allowlist``/``denylist`` are deliberately not read
    # — they are per-call path and command policy only Vibe's live resolver can
    # answer, and the child runs under that resolver rather than a snapshot of it.
    enabled = _glob_patterns(overrides.get("enabled_tools"))
    disabled = _glob_patterns(overrides.get("disabled_tools"))
    narrowed = {
        name
        for name in available_tools
        if (not enabled or name_matches(name, enabled))
        and not (disabled and name_matches(name, disabled))
    }
    per_tool = overrides.get("tools")
    per_tool = per_tool if isinstance(per_tool, Mapping) else {}

    def permission(name: str) -> ToolPermission:
        entry = per_tool.get(name)
        declared = entry.get("permission") if isinstance(entry, Mapping) else None
        if not isinstance(declared, str):
            return permission_of(name)
        try:
            return ToolPermission(declared)
        except ValueError:
            return permission_of(name)

    # Never `bypass_approval`: a profile is not a place to retire the resolver, and
    # False is what turns a declared `always` into `ask`.
    return _rust_tool_modes(narrowed, permission, bypass_approval=False)


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
                command=server.command,
                args=server.args,
                env=server.env,
                cwd=server.cwd,
            )
        case _:
            raise TypeError(f"Unsupported session MCP server: {type(server).__name__}")


async def _apply_cached_experiment_variants(
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> None:
    """Apply the last cached experiment variants to config before first render."""
    cached = load_cached_eval_response(config_orchestrator.config)
    if cached is None:
        return
    variants = config_variants_from_response(cached)
    if not variants:
        return
    try:
        layer = config_orchestrator.get_layer(GrowthbookLayer.NAME)
    except KeyError:
        return
    if isinstance(layer, GrowthbookLayer):
        layer.set_variants(variants)
        await config_orchestrator.reload()


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


def _require_session_logging(config: VibeConfigSchema) -> None:
    if config.session_logging.enabled:
        return
    raise RuntimeSessionNotFoundError(
        "Session logging is disabled. Enable it in config to use --continue or --resume"
    )


def _find_session_to_continue(config: VibeConfigSchema, *, cwd: Path) -> str:
    cwd = cwd.resolve()
    pointer_session_id = last_session_pointer.load(config.session_logging)
    if pointer_session_id is not None:
        session = SessionLoader.find_session_by_id(
            pointer_session_id, config.session_logging, working_directory=cwd
        )
        if session is not None:
            return pointer_session_id

    session = SessionLoader.find_latest_session(
        config.session_logging, working_directory=cwd
    )
    if session is not None:
        _, metadata = SessionLoader.load_session(session)
        session_id = metadata.get("session_id")
        if isinstance(session_id, str) and session_id:
            return session_id
        raise RuntimeSessionNotFoundError(f"Saved session has no session ID: {session}")

    message = (
        f"No previous sessions found in {config.session_logging.save_dir} for cwd={cwd}"
    )
    if cwd.is_relative_to(WORKTREES_DIR.path.resolve()):
        message = (
            f"{message}. This worktree has no sessions yet; start a new one or "
            "use --resume <ID> to continue an existing session here"
        )
    raise RuntimeSessionNotFoundError(message)


def _load_session(
    config: VibeConfigSchema, session_id: str
) -> tuple[Path, list[LLMMessage], dict[str, object]]:
    session_path = SessionLoader.find_session_by_id(session_id, config.session_logging)
    if session_path is None:
        raise RuntimeSessionNotFoundError(session_id)
    loaded_messages, metadata = SessionLoader.load_session(session_path)
    return session_path, loaded_messages, metadata


def _resolve_resume_session_id(config: VibeConfigSchema, session_id: str) -> str:
    legacy = resolve_legacy_session_reference(session_id, config.session_logging)
    if legacy is not None:
        return legacy.session_id
    return _resolve_unified_session_id(config, session_id) or session_id


def _resolve_unified_session_id(
    config: VibeConfigSchema, session_id: str
) -> str | None:
    unified_root = Path(config.session_logging.save_dir) / "unified"
    exact = unified_root / session_id
    if (exact / "CURRENT").is_file():
        return session_id
    if not unified_root.exists() or len(session_id) > _SHORT_SESSION_ID_LENGTH:
        return None
    matches = sorted(
        path.name
        for path in unified_root.iterdir()
        if path.is_dir()
        and path.name[:_SHORT_SESSION_ID_LENGTH] == session_id
        and (path / "CURRENT").is_file()
    )
    if len(matches) > 1:
        raise RuntimeConfigurationError(
            f"Unified session ID is ambiguous: {session_id}"
        )
    return matches[0] if matches else None


def _acquire_session_lease(
    config: VibeConfigSchema, session_id: str
) -> SessionLease | None:
    if not config.session_logging.enabled:
        return None
    return SessionLease(Path(config.session_logging.save_dir), session_id).acquire()


def _load_unified_import(
    config: VibeConfigSchema, session_id: str
) -> _ImportedSession | None:
    try:
        legacy_source = export_legacy_committed_history(
            session_id, config.session_logging
        )
    except InvalidLegacyInteropSourceError as exc:
        raise RuntimeConfigurationError(str(exc)) from exc
    if legacy_source is not None:
        raise RuntimeConfigurationError(
            f"Legacy session exists but could not be resumed: {session_id}"
        )
    session_id = _resolve_unified_session_id(config, session_id) or session_id
    session_root = Path(config.session_logging.save_dir) / "unified" / session_id
    if not (session_root / "CURRENT").is_file():
        return None
    try:
        from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
            UnifiedSessionStore,
        )
    except ImportError as exc:
        raise RuntimeInvalidMigrationSourceError(
            session_id,
            "unified",
            "The Unified Harness package is required to import this session",
        ) from exc
    try:
        stored = UnifiedSessionStore(
            Path(config.session_logging.save_dir), session_id
        ).load()
    except Exception as exc:
        raise RuntimeInvalidMigrationSourceError(
            session_id,
            "unified",
            f"Unified session store is invalid: {session_id}: {exc}",
        ) from exc
    if (
        not stored.runtime_state.quiescent
        or stored.journal
        or stored.interop_export is None
    ):
        raise RuntimeUnfinishedMigrationError(session_id, "unified")
    try:
        messages, provenance = import_unified_committed_history(
            stored.interop_export.model_dump(mode="json", by_alias=True)
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeInvalidMigrationSourceError(
            session_id,
            "unified",
            f"Unified committed history is invalid: {session_id}: {exc}",
        ) from exc
    provenance["imported_at"] = (
        datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
    metadata = stored.runtime_state.session_metadata
    return _ImportedSession(
        session_id=stored.manifest.session_id,
        cwd=metadata.cwd,
        root_session_id=metadata.root_session_id,
        parent_session_id=metadata.parent_session_id,
        messages=messages,
        provenance=dict(provenance),
        active_model=metadata.active_model,
    )


async def _restore_session_active_model(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    active_model: str | None,
    *,
    clear_existing: bool = False,
) -> None:
    if active_model is not None:
        failures = await set_session_active_model_override(
            orchestrator, active_model, reason="restore session active model"
        )
    elif clear_existing:
        failures = await clear_session_active_model_override(
            orchestrator, reason="clear previous session active model"
        )
    else:
        return
    if failures:
        raise RuntimeConfigurationError(
            f"Failed to restore session active model: {failures[0]}"
        )


def _parent_session_id(metadata: dict[str, object]) -> str | None:
    value = metadata.get("parent_session_id")
    return value if isinstance(value, str) else None


def _build_stats(loop: AgentLoop, metadata: dict[str, object]) -> AgentStats | None:
    if not isinstance(raw_stats := metadata.get("stats"), dict):
        return None
    stats = AgentStats.model_validate(raw_stats)
    if stats.cached_input_price_per_million is None:
        try:
            stats.cached_input_price_per_million = (
                loop.config.get_active_model().cached_input_price
            )
        except ValueError:
            pass
    return stats


def _apply_stored_stats(loop: AgentLoop, metadata: dict[str, object]) -> None:
    stats = _build_stats(loop, metadata)
    if stats is not None:
        loop.stats = stats


def _messages_for_fork(source: AgentLoop, message_id: str | None) -> list[LLMMessage]:
    messages = [
        message for message in source.messages if message.role is not Role.system
    ]
    if message_id is None:
        return [message.model_copy(deep=True) for message in messages]

    anchor = next(
        (
            index
            for index, message in enumerate(messages)
            if message.message_id == message_id
        ),
        None,
    )
    if anchor is None:
        raise ValueError(f"Cannot fork from unknown message_id: {message_id}")
    if messages[anchor].role is not Role.user:
        raise ValueError("Fork from message_id is only supported for user messages")

    end = next(
        (
            index
            for index, message in enumerate(messages[anchor + 1 :], start=anchor + 1)
            if message.role is Role.user
        ),
        len(messages),
    )
    return [message.model_copy(deep=True) for message in messages[:end]]


async def close_agent_loop(agent_loop: AgentLoop) -> None:
    errors: list[BaseException] = []
    for cleanup in (agent_loop.aclose, agent_loop.telemetry_client.aclose):
        try:
            await cleanup()
        except BaseException as exc:
            errors.append(exc)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise BaseExceptionGroup("Failed to close agent runtime", errors)
