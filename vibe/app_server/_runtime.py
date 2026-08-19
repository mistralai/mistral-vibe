from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue, ValidationError

from vibe import __version__
from vibe._experimental_harness import (
    ExperimentalHarnessUnavailableError,
    create_experimental_harness_host,
)
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._projection import project_agent_summaries, project_config_view
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server.client import AppServerClient
from vibe.app_server.client_tools import ClientToolHandler
from vibe.app_server.models import AgentStatsSnapshot, ConnectorCounts, MCPState
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
from vibe.core.experiments.manager import config_variants_from_response
from vibe.core.experiments.models import EvalResponse
from vibe.core.hooks.config import load_hooks_from_fs
from vibe.core.hooks.models import HookConfigResult
from vibe.core.paths import WORKTREES_DIR
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
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.tools.permissions import PermissionStore
from vibe.core.tracing import setup_tracing
from vibe.core.types import AgentStats, LLMMessage, Role, SessionMetadata
from vibe.core.utils import get_windows_bash_path, is_windows
from vibe.observability.logging import logger, set_config_log_level
from vibe.utils.cache_store import FileSystemCacheStore

_SHORT_SESSION_ID_LENGTH = 8
type _CommandEnvironmentMode = Literal["unix", "git_bash", "powershell"]


def _command_environment_mode() -> _CommandEnvironmentMode:
    if not is_windows():
        return "unix"
    if get_windows_bash_path() is not None:
        return "git_bash"
    return "powershell"


if TYPE_CHECKING:
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionContext
    from vibe.app_server.server import AppServer


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
            cache_store=self.policy.cache_store,
            force_bypass_tool_permissions=self.policy.force_bypass_tool_permissions,
            local_managed_shell_runtime_enabled=(
                self.policy.local_managed_shell_runtime_enabled
            ),
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
        try:
            try:
                session_path, loaded_messages, metadata = await asyncio.to_thread(
                    _load_session, source.config, session_id
                )
            except RuntimeSessionNotFoundError:
                imported = await asyncio.to_thread(
                    _load_unified_import, source.config, session_id
                )
                if imported is None:
                    raise
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
        ).build()
        return replacement


class HarnessProcess:
    def __init__(
        self,
        harness_files: HarnessFilesManager | None = None,
        *,
        experimental_harness: bool = False,
    ) -> None:
        self.runtime_factory = AgentRuntimeFactory()
        self.cache_store = FileSystemCacheStore()
        self.harness_files = harness_files or HarnessFilesManager(
            sources=("user", "project")
        )
        self.host_handler = HostRequestHandler(self.harness_files)
        self._configuration_lock = threading.Lock()
        self._configured = False
        self._staged_roots: dict[str, AgentLoop] = {}
        self._staged_roots_lock = asyncio.Lock()
        self._closed = False
        self._experimental_harness = experimental_harness

    def create_session_backend_host(
        self, services: SessionBackendServices
    ) -> SessionBackendHost:
        if self._experimental_harness:
            try:
                host = create_experimental_harness_host()
                from vibe.app_server._unified_harness_backend_adapter import (
                    adapt_harness_host,
                )
            except (ExperimentalHarnessUnavailableError, ImportError) as exc:
                raise RuntimeConfigurationError(str(exc)) from exc
            return adapt_harness_host(host, self.build_unified_session_context)

        from vibe.app_server._legacy_session_runtime import (
            create_legacy_session_backend_host,
        )

        return create_legacy_session_backend_host(
            open_root=self.open_root,
            runtime_factory=self.runtime_factory,
            host_handler=self.host_handler,
            stage_root=self.stage_root,
            services=services,
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

    async def build_unified_session_context(
        self, options: SessionOptions
    ) -> UnifiedSessionContext:
        from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
            RustContextSettings,
            RustDisabledCompactionPolicy,
            RustDisabledRuntimeToolFeature,
            RustGitBashCommandEnvironment,
            RustHarnessConfig,
            RustHarnessSettings,
            RustPowerShellCommandEnvironment,
            RustProgrammaticToolSettings,
            RustToolSettings,
            RustTurnSettings,
            RustUnixCommandEnvironment,
        )
        from mistralai_rust_harness.vibe import (  # pyright: ignore[reportMissingImports]
            LegacyImportSource,
            LegacySessionReference as HarnessLegacySessionReference,
            LocalRuntimeAdapterConfig,
        )
        from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
            PluginLockV1,
            sha256_json,
        )

        from vibe.app_server._unified_harness_backend_adapter import (
            UnifiedSessionContext,
        )

        session_config = await self._build_session_config(options)
        config = session_config.config_orchestrator.config
        active_model = config.get_active_model()
        provider = config.get_provider_for_model(active_model)
        cwd = Path(options.cwd or Path.cwd()).expanduser().resolve()
        workspace_roots = tuple(
            Path(root).expanduser().resolve() for root in options.workspace_roots
        ) or (cwd,)
        match _command_environment_mode():
            case "unix":
                command_environment = RustUnixCommandEnvironment()
            case "git_bash":
                command_environment = RustGitBashCommandEnvironment()
            case "powershell":
                command_environment = RustPowerShellCommandEnvironment()

        def resolve_legacy_source(
            session_id: str,
        ) -> HarnessLegacySessionReference | None:
            reference = resolve_legacy_session_reference(
                session_id, config.session_logging
            )
            if reference is None:
                return None
            return HarnessLegacySessionReference(
                session_id=reference.session_id,
                cwd=reference.cwd,
                root_session_id=reference.root_session_id,
                parent_session_id=reference.parent_session_id,
            )

        def load_legacy_source(session_id: str) -> LegacyImportSource:
            try:
                export = export_legacy_committed_history(
                    session_id, config.session_logging
                )
            except InvalidLegacyInteropSourceError as exc:
                return LegacyImportSource(state="invalid", error=str(exc))
            if export is None:
                return LegacyImportSource(state="absent")
            return LegacyImportSource(
                state="quiescent",
                reference=HarnessLegacySessionReference(
                    session_id=export.reference.session_id,
                    cwd=export.reference.cwd,
                    root_session_id=export.reference.root_session_id,
                    parent_session_id=export.reference.parent_session_id,
                ),
                store_revision=export.store_revision,
                history=export.history,
            )

        return UnifiedSessionContext(
            runtime=build_runtime_snapshot(
                options,
                session_config.config_orchestrator,
                session_config.harness_files,
            ),
            storage_root=config.session_logging.save_dir,
            legacy_source_loader=load_legacy_source,
            legacy_source_resolver=resolve_legacy_source,
            core_config=RustHarnessConfig(
                task_id="runtime-template",
                settings=RustHarnessSettings(
                    turn=RustTurnSettings(max_iterations=options.max_turns or 25),
                    context=RustContextSettings(
                        compaction=RustDisabledCompactionPolicy()
                    ),
                    tools=RustToolSettings(
                        programmatic=RustProgrammaticToolSettings(
                            max_effects=128, max_operations=1024
                        ),
                        subagents=RustDisabledRuntimeToolFeature(),
                        background_processes=RustDisabledRuntimeToolFeature(),
                        command_environment=command_environment,
                    ),
                ),
            ),
            plugin_lock=PluginLockV1(
                environment_sha256=sha256_json({"plugins": []}), plugins=[]
            ),
            adapter_config=LocalRuntimeAdapterConfig(
                provider=provider.name,
                base_url=provider.api_base,
                api_key=resolve_api_key(provider.api_key_env_var)
                if provider.api_key_env_var
                else None,
                model=active_model.name,
                temperature=active_model.temperature,
                timeout_s=config.api_timeout,
                retry_max_elapsed_time_s=config.api_retry_max_elapsed_time,
                cwd=cwd,
                workspace_roots=workspace_roots,
                env=dict(os.environ),
                bypass_approval=options.auto_approve,
                tool_modes={
                    "file_system.read_file": "allow",
                    "file_system.write_file": "allow",
                    "file_system.bash": "allow",
                },
            ),
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
        )

    async def open_root(self, request: RootOpenRequest) -> AgentLoop:
        if self._experimental_harness:
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
) -> HarnessServer:
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
        ),
        _transport=transport,
        _reconnectable=transport_kind == "in_process",
    )


def build_runtime_snapshot(
    options: SessionOptions,
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema],
    harness_files: HarnessFilesManager,
) -> RuntimeSnapshot:
    config = config_orchestrator.config
    agents = AgentManager(
        config_orchestrator,
        options.agent or config.default_agent,
        harness_files=harness_files,
    )
    active, available = project_agent_summaries(
        agents.active_profile, agents.available_agents.values()
    )
    return RuntimeSnapshot(
        config=project_config_view(config),
        active_agent=active,
        agents=available,
        skills=[],
        tools=[],
        stats=AgentStatsSnapshot(),
        context_window=config.get_active_model().auto_compact_threshold,
        issues=[],
        hooks_count=0,
        connectors=ConnectorCounts(),
        mcp=MCPState(),
    )


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
        from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
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
