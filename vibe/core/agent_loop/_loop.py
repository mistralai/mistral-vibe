from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator, Sequence
import contextlib
import copy
from dataclasses import dataclass
from enum import StrEnum, auto
from functools import wraps
from http import HTTPStatus
import inspect
import json
import os
from pathlib import Path
import shutil
import threading
from threading import Thread
import time
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from vibe.core.agent_loop_hooks import AgentLoopHooksMixin
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentProfile, BuiltinAgentName
from vibe.core.autocompletion.path_prompt import build_path_prompt_payload
from vibe.core.cache_store import InMemoryVibeCodeCacheStore, VibeCodeCacheStore
from vibe.core.checkpoints import Checkpointer, CheckpointRecorder, FileStore
from vibe.core.compaction import (
    CompactionFailedError as CompactionFailedError,
    CompactionManager,
)
from vibe.core.compaction.context import (
    extract_summary,
    render_teleport_summary_request,
)
from vibe.core.config import AnyVibeConfig, ModelConfig, ProviderConfig, resolve_api_key
from vibe.core.config.orchestrator_legacy import LegacyConfigOrchestrator
from vibe.core.experiments import ExperimentManager
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.session import (
    hydrate_experiments_from_session as session_hydrate_experiments_from_session,
    initialize_experiments as session_initialize_experiments,
)
from vibe.core.hooks.manager import HooksManager
from vibe.core.hooks.models import HookConfigResult, HookEvent
from vibe.core.llm.backend.factory import create_backend
from vibe.core.llm.exceptions import BackendError
from vibe.core.llm.format import (
    APIToolFormatHandler,
    FailedToolCall,
    ResolvedMessage,
    ResolvedToolCall,
)
from vibe.core.llm.types import BackendLike
from vibe.core.middleware import (
    CHAT_AGENT_EXIT,
    CHAT_AGENT_REMINDER,
    PLAN_AGENT_EXIT,
    AutoCompactMiddleware,
    ContextWarningMiddleware,
    ConversationContext,
    MiddlewareAction,
    MiddlewarePipeline,
    MiddlewareResult,
    PriceLimitMiddleware,
    ReadOnlyAgentMiddleware,
    ResetReason,
    TokenLimitMiddleware,
    TurnLimitMiddleware,
    make_plan_agent_reminder,
)
from vibe.core.plan_session import PlanSession
from vibe.core.review import ReviewManager
from vibe.core.rewind import RewindManager
from vibe.core.scratchpad import init_scratchpad
from vibe.core.session.session_id import extract_suffix, generate_session_id
from vibe.core.session.session_logger import SessionLogger
from vibe.core.session.session_migration import migrate_sessions_entrypoint
from vibe.core.skills.manager import SkillManager
from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.telemetry.build_metadata import (
    build_attachment_counts,
    build_request_metadata,
)
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import (
    LaunchContext,
    ProjectPickerTelemetryPayload,
    TelemetryCallType,
    TelemetryRequestMetadata,
)
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.telemetry import TeleportTelemetryTracker
from vibe.core.teleport.types import (
    TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH,
    TeleportCompleteEvent,
    TeleportMessageContext,
    TeleportMessageContextSource,
    TeleportSummarizingContextEvent,
)
from vibe.core.tools.base import (
    BaseTool,
    CancellableToolResult,
    InvokeContext,
    ToolError,
    ToolPermission,
    ToolPermissionError,
)
from vibe.core.tools.builtins.read_file import ReadFileArgs
from vibe.core.tools.builtins.skill import (
    Skill as SkillTool,
    SkillArgs,
    select_skill_result,
    skill_content_marker,
)
from vibe.core.tools.manager import NoSuchToolError, ToolManager
from vibe.core.tools.permissions import (
    ApprovedRule,
    PermissionContext,
    PermissionStore,
    RequiredPermission,
)
from vibe.core.tracing import (
    agent_span,
    build_otel_span_exporter_config,
    set_tool_result,
    tool_span,
)
from vibe.core.trusted_folders import has_agents_md_file
from vibe.core.types import (
    AgentProfileChangedEvent,
    AgentStats,
    ApprovalCallback,
    ApprovalResponse,
    AssistantEvent,
    AvailableTool,
    BaseEvent,
    CompactEndEvent,
    CompactStartEvent,
    ContextClearedEvent,
    ContextTooLongError,
    FunctionCall,
    ImageAttachment,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    MessageList,
    PlanReviewEndedEvent,
    PlanReviewRequestedEvent,
    RateLimitError,
    ReasoningEvent,
    RefusalError,
    ResponseTooLongError,
    Role,
    SessionTitleUpdatedEvent,
    StrToolChoice,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserDisplayContentMetadata,
    UserInputCallback,
    UserMessageEvent,
)
from vibe.core.utils import (
    TOOL_ERROR_TAG,
    VIBE_STOP_EVENT_TAG,
    VIBE_WARNING_TAG,
    CancellationReason,
    get_server_url_from_api_base,
    get_user_agent,
    get_user_cancellation_message,
    is_user_cancellation_event,
)


def _is_git_executable_available() -> bool:
    executable = os.environ.get("GIT_PYTHON_GIT_EXECUTABLE")
    if not executable:
        return shutil.which("git") is not None

    path = Path(executable).expanduser()
    if path.is_absolute() or os.sep in executable:
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(executable) is not None


_TELEPORT_AVAILABLE = _is_git_executable_available()


def _load_teleport_service() -> type[TeleportService]:
    try:
        from vibe.core.teleport.teleport import TeleportService
    except ImportError as e:
        raise TeleportError(
            "Teleport requires git to be installed. Please install git and try again."
        ) from e
    return TeleportService


if TYPE_CHECKING:
    from opentelemetry import trace

    from vibe.core.config.orchestrator_port import ConfigOrchestratorPort
    from vibe.core.teleport.teleport import TeleportService
    from vibe.core.teleport.types import TeleportPushResponseEvent, TeleportYieldEvent
    from vibe.core.tools.connectors.connector_registry import ConnectorRegistry
    from vibe.core.tools.mcp.pool import MCPConnectionPool
    from vibe.core.tools.mcp.registry import MCPRegistry
    from vibe.core.tools.mcp_sampling import MCPSamplingHandler


class ToolExecutionResponse(StrEnum):
    SKIP = auto()
    EXECUTE = auto()


class ToolDecision(BaseModel):
    verdict: ToolExecutionResponse
    approval_type: ToolPermission
    feedback: str | None = None


class _SwappableConfigSource:
    """Config getter for reload-prepared managers.

    Points at the target agent's config while preparation runs off-loop, then is
    repointed to the live config inside the synchronous commit. The prepared
    managers are not shared with the running turn until commit, so the running
    turn only ever observes the live getter.
    """

    def __init__(self, getter: Callable[[], AnyVibeConfig]) -> None:
        self._getter = getter

    def get(self) -> AnyVibeConfig:
        return self._getter()

    def point_to(self, getter: Callable[[], AnyVibeConfig]) -> None:
        self._getter = getter


@dataclass(frozen=True, slots=True)
class _PreparedReload:
    backend: BackendLike
    tool_manager: ToolManager
    skill_manager: SkillManager
    system_prompt: str
    config_source: _SwappableConfigSource


class AgentLoopError(Exception):
    """Base exception for AgentLoop errors."""


class AgentLoopStateError(AgentLoopError):
    """Raised when agent loop is in an invalid state."""


class AgentLoopLLMResponseError(AgentLoopError):
    """Raised when LLM response is malformed or missing expected data."""


class ImagesNotSupportedError(AgentLoopError):
    """Raised when the active model does not support image attachments."""


class TeleportError(AgentLoopError):
    """Raised when teleport to Vibe Code fails."""


def _refusal_error(provider: str, model: str, chunk: LLMChunk) -> RefusalError:
    stop = chunk.stop
    return RefusalError(
        provider,
        model,
        category=stop.category if stop else None,
        explanation=stop.explanation if stop else None,
    )


def _should_raise_rate_limit_error(e: Exception) -> bool:
    return isinstance(e, BackendError) and e.status == HTTPStatus.TOO_MANY_REQUESTS


def _is_context_too_long_error(e: Exception) -> bool:
    if isinstance(e, BackendError):
        return e.is_context_too_long
    if isinstance(e, RuntimeError) and isinstance(e.__cause__, BackendError):
        return e.__cause__.is_context_too_long
    return False


def _is_response_too_long_error(e: Exception) -> bool:
    if isinstance(e, BackendError):
        return e.is_response_too_long
    if isinstance(e, RuntimeError) and isinstance(e.__cause__, BackendError):
        return e.__cause__.is_response_too_long
    return False


def _is_non_retryable_error(e: BaseException) -> bool:
    # Detect Temporal-style ``non_retryable`` flag without importing temporalio.
    # Walks ``__cause__`` so an ``ActivityError`` whose cause is a non-retryable
    # ``ApplicationError`` is detected too — that's what callers driving the
    # agent loop from a Temporal activity will see when a sub-activity has
    # already failed terminally.
    seen: set[int] = set()
    current: BaseException | None = e
    while current is not None and id(current) not in seen:
        if getattr(current, "non_retryable", False):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


def requires_init(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that awaits deferred initialization before executing the method."""
    if inspect.isasyncgenfunction(fn):

        @wraps(fn)
        async def gen_wrapper(self: AgentLoop, *args: Any, **kwargs: Any) -> Any:
            await self.wait_until_ready()
            agen = fn(self, *args, **kwargs)
            sent: Any = None
            try:
                while True:
                    sent = yield await agen.asend(sent)
            except StopAsyncIteration:
                return
            finally:
                await agen.aclose()

        return gen_wrapper

    @wraps(fn)
    async def wrapper(self: AgentLoop, *args: Any, **kwargs: Any) -> Any:
        await self.wait_until_ready()
        return await fn(self, *args, **kwargs)

    return wrapper


class AgentLoop(AgentLoopHooksMixin):  # noqa: PLR0904
    def __init__(  # noqa: PLR0913, PLR0915
        self,
        config_orchestrator: ConfigOrchestratorPort[AnyVibeConfig],
        *,
        agent_name: str = BuiltinAgentName.DEFAULT,
        message_observer: Callable[[LLMMessage], None] | None = None,
        max_turns: int | None = None,
        max_price: float | None = None,
        max_tokens: int | None = None,
        max_session_tokens: int | None = None,
        backend: BackendLike | None = None,
        enable_streaming: bool = False,
        launch_context: LaunchContext | None = None,
        is_subagent: bool = False,
        defer_heavy_init: bool = False,
        headless: bool = False,
        hook_config_result: HookConfigResult | None = None,
        permission_store: PermissionStore | None = None,
        mcp_registry: MCPRegistry | None = None,
        cache_store: VibeCodeCacheStore | None = None,
        force_bypass_tool_permissions: bool = False,
    ) -> None:
        self._config_orchestrator = config_orchestrator
        config = config_orchestrator.config
        self._force_bypass_tool_permissions = force_bypass_tool_permissions
        self._apply_forced_bypass()
        self._headless = headless
        self.cache_store = cache_store or InMemoryVibeCodeCacheStore()

        self._defer_heavy_init = defer_heavy_init
        self._deferred_init_thread: threading.Thread | None = None
        self._deferred_init_lock = threading.Lock()
        self._init_error: Exception | None = None
        self._init_start_time = time.monotonic()
        self._experiments_task: asyncio.Task[None] | None = None
        self._reload_generation: int = 0
        self._pending_new_session_telemetry: bool = False
        self._ready_telemetry_pending: bool = defer_heavy_init

        self._permission_store = permission_store or PermissionStore()

        self.mcp_registry: MCPRegistry | None = (
            mcp_registry
            if defer_heavy_init
            else mcp_registry or self._create_mcp_registry()
        )
        self._mcp_pool: MCPConnectionPool | None = (
            None if defer_heavy_init else self._create_mcp_pool()
        )
        self.connector_registry: ConnectorRegistry | None = (
            None if defer_heavy_init else self._create_connector_registry()
        )
        self.agent_manager = AgentManager(
            self._config_orchestrator,
            initial_agent=agent_name,
            allow_subagent=is_subagent,
        )
        self.tool_manager = ToolManager(
            lambda: self.config,
            mcp_registry=self.mcp_registry,
            connector_registry=self.connector_registry,
            defer_mcp=True,
            permission_getter=self._permission_store.get_tool_permission,
        )
        self.skill_manager = SkillManager(lambda: self.config)
        self.message_observer = message_observer
        self._max_turns = max_turns
        self._max_price = max_price
        self._max_tokens = max_tokens
        self._max_session_tokens = max_session_tokens
        self._plan_session = PlanSession()
        self._user_plan: str | None = None

        self.format_handler = APIToolFormatHandler()

        self._injected_backend = backend
        self.backend = self.backend_factory()
        self._sampling_handler = self._create_sampling_handler(
            backend_getter=lambda: self.backend,
            config_getter=lambda: self.config,
            metadata_getter=lambda: self._build_backend_metadata(
                call_type="secondary_call"
            ).model_dump(exclude_none=True),
            extra_headers_getter=self._get_extra_headers,
        )

        self.enable_streaming = enable_streaming
        self.middleware_pipeline = MiddlewarePipeline()
        self._setup_middleware()

        self.session_id = generate_session_id()
        self.parent_session_id: str | None = None
        self.scratchpad_dir = (
            init_scratchpad(self.session_id) if not is_subagent else None
        )

        self.messages = MessageList(initial=[], observer=message_observer)

        self.stats = AgentStats()
        self.approval_callback: ApprovalCallback | None = None
        self.user_input_callback: UserInputCallback | None = None
        self.launch_context = launch_context

        try:
            active_model = config.get_active_model()
            self.stats.input_price_per_million = active_model.input_price
            self.stats.output_price_per_million = active_model.output_price
        except ValueError:
            pass

        self._current_user_message_id: str | None = None
        self._is_user_prompt_call: bool = False
        self._reactive_recovery_used: bool = False
        self._pending_injected_messages: list[LLMMessage] = []
        self._pending_clear_context: bool = False

        self.experiment_manager = ExperimentManager(
            client=RemoteEvalClient.from_settings(
                api_host=config.experiments.api_host,
                client_key=config.experiments.client_key,
            ),
            overrides=dict(config.experiment_overrides),
        )
        self.telemetry_client = TelemetryClient(
            config_getter=lambda: self.config,
            session_id_getter=lambda: self.session_id,
            parent_session_id_getter=lambda: self.parent_session_id,
            launch_context=self.launch_context,
            experiments_getter=lambda: self.experiment_manager.assignments(),
            user_plan_getter=lambda: self.user_plan,
        )
        self.session_logger = SessionLogger(config.session_logging, self.session_id)
        self._hook_config_result = hook_config_result
        self._hooks_manager = (
            HooksManager(hook_config_result.hooks) if hook_config_result else None
        )
        self.hook_config_issues = (
            hook_config_result.issues if hook_config_result else []
        )
        self.hooks_count = len(hook_config_result.hooks) if hook_config_result else 0
        checkpointer = Checkpointer()
        file_store = FileStore()
        self.checkpoint_recorder = CheckpointRecorder(
            checkpointer, self.messages, file_store
        )
        self.review_manager = ReviewManager(checkpointer, file_store)
        self.rewind_manager = RewindManager(
            checkpointer,
            messages=self.messages,
            save_messages=self._save_messages,
            reset_session=self._reset_session,
            files=file_store,
        )
        self.compaction_manager = CompactionManager(
            messages=self.messages,
            stats_getter=lambda: self.stats,
            config_getter=lambda: self.config,
            complete=self._complete,
            available_tools=lambda: self.format_handler.get_available_tools(
                self.tool_manager
            ),
            tool_choice=self.format_handler.get_tool_choice,
            save=self._save_messages,
            reset_session=self._reset_session,
            telemetry_client=self.telemetry_client,
            session_ids=lambda: (self.session_id, self.parent_session_id),
        )
        self._teleport_service: TeleportService | None = None

        Thread(
            target=migrate_sessions_entrypoint,
            args=(config.session_logging,),
            daemon=True,
            name="migrate_sessions",
        ).start()

        if defer_heavy_init:
            self._start_deferred_init()
        else:
            self._complete_init()
            if err := self._init_error:
                raise err

    def _start_deferred_init(self) -> threading.Thread:
        """Spawn a daemon thread that finishes deferred heavy I/O once."""
        with self._deferred_init_lock:
            if self._deferred_init_thread is not None:
                return self._deferred_init_thread

            thread = threading.Thread(
                target=self._complete_init, daemon=True, name="agent_loop_init"
            )
            self._deferred_init_thread = thread
            thread.start()
            return thread

    @property
    def is_initialized(self) -> bool:
        """Whether deferred initialization has completed (successfully or not)."""
        if not self._defer_heavy_init:
            return True
        thread = self._deferred_init_thread
        return thread is not None and not thread.is_alive()

    def _complete_init(self) -> None:
        """Run deferred heavy I/O: MCP and connector discovery.

        Intended to be called from a background thread when
        ``defer_heavy_init=True`` was passed to ``__init__``.
        """
        try:
            self._ensure_remote_registries()
            self.tool_manager.integrate_all(raise_on_mcp_failure=True)
            self.messages.update_system_prompt(self._build_system_prompt(), notify=True)
        except Exception as exc:
            self._init_error = exc

    async def wait_until_ready(self) -> None:
        """Await deferred initialization (MCP + experiments) from an async context."""
        if self._defer_heavy_init:
            thread = self._start_deferred_init()
            await asyncio.to_thread(thread.join)
            if err := self._init_error:
                raise copy.copy(err).with_traceback(err.__traceback__)
        if (task := self._experiments_task) is not None:
            if task is asyncio.current_task():
                return
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._ready_telemetry_pending:
            self._ready_telemetry_pending = False
            duration = int((time.monotonic() - self._init_start_time) * 1000)
            self.emit_ready_telemetry(duration)
        if self._pending_new_session_telemetry:
            self._pending_new_session_telemetry = False
            self.emit_new_session_telemetry()

    @property
    def agent_profile(self) -> AgentProfile:
        return self.agent_manager.active_profile

    @property
    def config_orchestrator(self) -> ConfigOrchestratorPort[AnyVibeConfig]:
        return self._config_orchestrator

    @property
    def base_config(self) -> AnyVibeConfig:
        return self._config_orchestrator.config

    @property
    def config(self) -> AnyVibeConfig:
        return self.agent_manager.config

    @property
    def bypass_tool_permissions(self) -> bool:
        return self.config.bypass_tool_permissions

    def _apply_forced_bypass(self) -> None:
        if self._force_bypass_tool_permissions:
            self.base_config.bypass_tool_permissions = True

    def _replace_base_config(self, config: AnyVibeConfig) -> None:
        # Sync bridge: swap the config on the shared orchestrator in place so
        # AgentManager sees it. PR5 construction sites always wrap a legacy
        # config; PR6 replaces these writes with async orchestrator calls.
        cast(LegacyConfigOrchestrator, self._config_orchestrator).replace_config(config)

    async def refresh_config(self) -> None:
        await self._config_orchestrator.reload()
        self._apply_forced_bypass()
        self.agent_manager.invalidate_config()
        if self.mcp_registry is not None:
            self.mcp_registry.sync_active_servers(self.config.mcp_servers)

    def _drain_pending_injections(self) -> bool:
        if not self._pending_injected_messages:
            return False
        for injected in self._pending_injected_messages:
            self.messages.append(injected)
        self._pending_injected_messages.clear()
        return True

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        self.approval_callback = callback

    def set_user_input_callback(self, callback: UserInputCallback) -> None:
        self.user_input_callback = callback

    async def set_tool_permission(
        self, tool_name: str, permission: ToolPermission, save_permanently: bool = False
    ) -> None:
        if save_permanently:
            await self._config_orchestrator.set_field(
                f"/tools/{tool_name}/permission", permission.value
            )

        self._permission_store.set_tool_permission(tool_name, permission)

    async def approve_always(
        self,
        tool_name: str,
        required_permissions: list[RequiredPermission] | None,
        save_permanently: bool = False,
    ) -> None:
        """Handle 'Allow Always' approval: add session rules or set tool-level permission."""
        if required_permissions:
            for rp in required_permissions:
                self._permission_store.add_rule(
                    ApprovedRule(
                        tool_name=tool_name,
                        scope=rp.scope,
                        session_pattern=rp.session_pattern,
                    )
                )
            if save_permanently and (
                allowlist_update := self.config.build_tool_allowlist_update(
                    tool_name, [rp.session_pattern for rp in required_permissions]
                )
            ):
                # Extract the allowlist from the update dict
                allowlist = allowlist_update["tools"][tool_name]["allowlist"]
                await self._config_orchestrator.set_field(
                    f"/tools/{tool_name}/allowlist", allowlist
                )
        else:
            await self.set_tool_permission(
                tool_name, ToolPermission.ALWAYS, save_permanently=save_permanently
            )

    def start_initialize_experiments(self) -> None:
        if self._experiments_task is not None:
            return
        self._pending_new_session_telemetry = True
        self._ready_telemetry_pending = True
        self._experiments_task = asyncio.create_task(self.initialize_experiments())

    async def initialize_experiments(self) -> None:
        updated = await session_initialize_experiments(
            config=self.config,
            manager=self.experiment_manager,
            session_logger=self.session_logger,
            launch_context=self.launch_context,
        )
        if updated:
            with contextlib.suppress(Exception):
                await self.refresh_system_prompt()

    async def hydrate_experiments_from_session(self) -> None:
        hydrated = await session_hydrate_experiments_from_session(
            config=self.config,
            manager=self.experiment_manager,
            session_logger=self.session_logger,
        )
        if hydrated:
            with contextlib.suppress(Exception):
                await self.refresh_system_prompt()

    def emit_new_session_telemetry(self) -> None:
        has_agents_md = has_agents_md_file(Path.cwd())
        nb_skills = len(self.skill_manager.available_skills)
        nb_mcp_servers = len(self.config.mcp_servers)
        nb_models = len(self.config.models)

        self.telemetry_client.send_new_session(
            has_agents_md=has_agents_md,
            nb_skills=nb_skills,
            nb_mcp_servers=nb_mcp_servers,
            nb_models=nb_models,
        )

    def emit_ready_telemetry(self, init_duration_ms: int) -> None:
        self.telemetry_client.send_ready(init_duration_ms=init_duration_ms)

    def emit_session_closed_telemetry(self) -> None:
        self.telemetry_client.send_session_closed()

    async def aclose(self) -> None:
        if (task := self._experiments_task) is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if self._mcp_pool is not None:
            with contextlib.suppress(Exception):
                await self._mcp_pool.aclose()
        with contextlib.suppress(Exception):
            await self.backend.__aexit__(None, None, None)
        with contextlib.suppress(Exception):
            await self.experiment_manager.aclose()

    def _create_connector_registry(self) -> ConnectorRegistry | None:
        if not self.base_config.enable_connectors:
            return None

        provider = self.base_config.get_mistral_provider()
        if provider is None:
            return None

        api_key_env = provider.api_key_env_var or "MISTRAL_API_KEY"
        api_key = resolve_api_key(api_key_env) or ""
        if not api_key:
            return None

        server_url = get_server_url_from_api_base(provider.api_base)
        from vibe.core.tools.connectors.connector_registry import ConnectorRegistry

        return ConnectorRegistry(api_key=api_key, server_url=server_url)

    @staticmethod
    def _create_mcp_registry() -> MCPRegistry:
        from vibe.core.tools.mcp.registry import MCPRegistry

        return MCPRegistry()

    @staticmethod
    def _create_mcp_pool() -> MCPConnectionPool:
        from vibe.core.tools.mcp.pool import MCPConnectionPool

        return MCPConnectionPool()

    def _ensure_remote_registries(self) -> None:
        if self.mcp_registry is None and self.config.mcp_servers:
            self.mcp_registry = self._create_mcp_registry()
            self.tool_manager.set_mcp_registry(self.mcp_registry)

        if self._mcp_pool is None and self.config.mcp_servers:
            self._mcp_pool = self._create_mcp_pool()

        if self.connector_registry is None:
            self.connector_registry = self._create_connector_registry()
            self.tool_manager.set_connector_registry(self.connector_registry)

    @staticmethod
    def _create_sampling_handler(
        *,
        backend_getter: Callable[[], BackendLike],
        config_getter: Callable[[], AnyVibeConfig],
        metadata_getter: Callable[[], dict[str, Any]],
        extra_headers_getter: Callable[[], dict[str, str]],
    ) -> MCPSamplingHandler:
        handler: MCPSamplingHandler | None = None

        async def lazy_handler(context: Any, params: Any) -> Any:
            nonlocal handler
            if handler is None:
                from vibe.core.tools.mcp_sampling import MCPSamplingHandler

                handler = MCPSamplingHandler(
                    backend_getter=backend_getter,
                    config_getter=config_getter,
                    metadata_getter=metadata_getter,
                    extra_headers_getter=extra_headers_getter,
                )
            return await handler(context, params)

        # Only ever invoked as a callable, never attribute-accessed.
        return cast("MCPSamplingHandler", lazy_handler)

    def _render_system_prompt(
        self,
        tool_manager: ToolManager,
        skill_manager: SkillManager,
        config: AnyVibeConfig | None = None,
    ) -> str:
        return get_universal_system_prompt(
            tool_manager,
            config or self.config,
            skill_manager,
            self.agent_manager,
            scratchpad_dir=self.scratchpad_dir,
            headless=self._headless,
            experiment_manager=self.experiment_manager,
        )

    def _build_system_prompt(self) -> str:
        return self._render_system_prompt(self.tool_manager, self.skill_manager)

    @requires_init
    async def refresh_system_prompt(self) -> None:
        """Rebuild and replace the system prompt with current tool/skill state."""
        self.messages.update_system_prompt(self._build_system_prompt())

    def backend_factory(self, config: AnyVibeConfig | None = None) -> BackendLike:
        return self._injected_backend or self._select_backend(config)

    def _select_backend(self, config: AnyVibeConfig | None = None) -> BackendLike:
        config = config or self.config
        provider = config.get_active_provider()
        return create_backend(
            provider=provider,
            timeout=config.api_timeout,
            retry_max_elapsed_time=config.api_retry_max_elapsed_time,
            enable_otel=(
                config.enable_telemetry
                and config.enable_otel
                and build_otel_span_exporter_config(
                    config.otel_endpoint, config.get_mistral_provider()
                )
                is not None
            ),
        )

    async def _save_messages(self, *, allow_empty: bool = False) -> None:
        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self.base_config,
            self.tool_manager,
            self.agent_profile,
            allow_empty=allow_empty,
        )

    @requires_init
    async def inject_user_context(
        self,
        content: str,
        *,
        as_message: bool = False,
        inject_implicit: bool = False,
        images: list[ImageAttachment] | None = None,
        client_message_id: str | None = None,
        on_event: Callable[[BaseEvent], Awaitable[None]] | None = None,
    ) -> None:
        if as_message:
            self.messages.append(
                LLMMessage(
                    role=Role.user,
                    content=content,
                    message_id=client_message_id or str(uuid4()),
                    images=images or None,
                )
            )
            if inject_implicit:
                async for event in self._inject_invoked_skill(content):
                    if on_event is not None:
                        await on_event(event)
                async for event in self._inject_mentioned_files(content):
                    if on_event is not None:
                        await on_event(event)
        else:
            self.messages.append(
                LLMMessage(
                    role=Role.user,
                    content=content,
                    injected=True,
                    images=images or None,
                )
            )
        await self._save_messages()

    @requires_init
    async def act(
        self,
        msg: str,
        client_message_id: str | None = None,
        *,
        auto_title: str | None = None,
        images: list[ImageAttachment] | None = None,
        user_display_content: UserDisplayContentMetadata | None = None,
    ) -> AsyncGenerator[BaseEvent, None]:
        try:
            active_model = self.config.get_active_model()
            model_name = active_model.name
        except ValueError:
            active_model = None
            model_name = None
        if images and active_model is not None and not active_model.supports_images:
            raise ImagesNotSupportedError(active_model.alias)
        self._clean_message_history()
        try:
            self.checkpoint_recorder.create_checkpoint()
            async with agent_span(model=model_name, session_id=self.session_id):
                async for event in self._conversation_loop(
                    msg,
                    client_message_id=client_message_id,
                    auto_title=auto_title,
                    images=images,
                    user_display_content=user_display_content,
                ):
                    yield event
        finally:
            # Seal the turn's post-edit boundary so per-turn review can attribute
            # later edits correctly, even if the turn opens then fails, or is
            # cancelled mid-flight.
            self.checkpoint_recorder.seal_turn()

    @property
    def teleport_service(self) -> TeleportService:
        if not _TELEPORT_AVAILABLE:
            raise TeleportError(
                "Teleport requires git to be installed. "
                "Please install git and try again."
            )

        if self._teleport_service is None:
            self._teleport_service = _load_teleport_service()(
                session_logger=self.session_logger,
                vibe_code_sessions_base_url=self.config.vibe_code_sessions_base_url,
                vibe_code_api_key=self.config.vibe_code_api_key,
                vibe_config=self.base_config,
            )
        return self._teleport_service

    @requires_init
    async def teleport_to_vibe_code(
        self,
        prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: ProjectPickerTelemetryPayload | None = None,
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        nb_session_messages = max(len(self.messages) - 1, 0)
        resolved_prompt = self._resolve_teleport_prompt(prompt)
        telemetry_tracker = TeleportTelemetryTracker(
            telemetry_client=self.telemetry_client,
            nb_session_messages=nb_session_messages,
            stage="no_history" if not resolved_prompt else "git_check",
            project_picker=project_picker,
        )
        try:
            teleport_message_context: TeleportMessageContext | None = None
            if resolved_prompt and self._should_summarize_teleport_context(prompt):
                summary_event = TeleportSummarizingContextEvent()
                telemetry_tracker.record_event(summary_event)
                yield summary_event
                try:
                    message_context = await self._summarize_teleport_context(
                        prompt=prompt, resolved_prompt=resolved_prompt
                    )
                except ServiceTeleportError:
                    telemetry_tracker.record_context_summary_failed()
                    raise
                except Exception as e:
                    telemetry_tracker.record_context_summary_failed()
                    raise ServiceTeleportError(
                        "Failed to summarize context for teleport.",
                        telemetry_details={"failure_kind": "context_summary_failed"},
                    ) from e

                teleport_message_context = self._build_teleport_message_context(
                    message_context, telemetry_tracker
                )
            async with self.teleport_service:
                gen = self.teleport_service.execute(
                    prompt=resolved_prompt,
                    project_id=project_id,
                    message_context=teleport_message_context,
                )
                response: TeleportPushResponseEvent | None = None
                while True:
                    try:
                        event = await gen.asend(response)
                        telemetry_tracker.record_event(event)
                        if isinstance(event, TeleportCompleteEvent):
                            telemetry_tracker.send_success()
                        response = yield event
                    except StopAsyncIteration:
                        break
        except ServiceTeleportError as e:
            telemetry_tracker.record_service_error(e)
            raise TeleportError(str(e)) from e
        except (asyncio.CancelledError, GeneratorExit):
            telemetry_tracker.record_cancelled()
            raise
        except Exception as e:
            telemetry_tracker.record_unexpected_error(e)
            raise
        finally:
            telemetry_tracker.send_failure_if_needed()
            self._teleport_service = None

    def _resolve_teleport_prompt(self, prompt: str | None) -> str:
        if prompt:
            return prompt

        last = self._last_user_message()
        content = last.content if last else None
        return content if isinstance(content, str) and content else ""

    def _build_teleport_message_context(
        self, summary: str, telemetry_tracker: TeleportTelemetryTracker
    ) -> TeleportMessageContext | None:
        try:
            message_context = TeleportMessageContext(
                summary=summary, source=self._teleport_message_context_source()
            )
        except ValidationError:
            telemetry_tracker.record_context_summary_failed()
            return None

        telemetry_tracker.record_context_summary_generated(summary)
        return message_context

    def _should_summarize_teleport_context(self, prompt: str | None) -> bool:
        if not self.config.experimental_teleport_context_summary:
            return False
        return any(
            self._is_teleport_context_message(message)
            for message in self._teleport_context_messages(prompt)
        )

    @staticmethod
    def _is_teleport_context_message(message: LLMMessage) -> bool:
        if message.role == Role.system:
            return False
        return bool(
            message.content
            or message.reasoning_content
            or message.tool_calls
            or message.tool_call_id
            or message.images
        )

    def _teleport_context_messages(self, prompt: str | None) -> list[LLMMessage]:
        excluded = None if prompt else self._last_user_message()
        return [message for message in self.messages if message is not excluded]

    async def _summarize_teleport_context(
        self, *, prompt: str | None, resolved_prompt: str
    ) -> str:
        source_messages = [
            message.model_copy(deep=True)
            for message in self._teleport_context_messages(prompt)
        ]
        summary_request = render_teleport_summary_request(
            self.config.compaction_prompt,
            resolved_prompt,
            max_summary_chars=TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH,
        )
        summary_messages = [
            *source_messages,
            LLMMessage(role=Role.user, content=summary_request),
        ]
        self.stats.steps += 1
        summary_result = await self._complete(
            model=self.config.get_compaction_model(),
            messages=summary_messages,
            tools=[],
            tool_choice=None,
            call_type="secondary_call",
        )
        raw_content = (summary_result.message.content or "").strip()
        if summary_result.message.tool_calls or not raw_content:
            raise ServiceTeleportError(
                "Failed to summarize context for teleport.",
                telemetry_details={"failure_kind": "context_summary_failed"},
            )
        return extract_summary(raw_content) or raw_content

    def _teleport_message_context_source(self) -> TeleportMessageContextSource:
        if self.launch_context is None:
            return TeleportMessageContextSource()
        return TeleportMessageContextSource(
            entrypoint=self.launch_context.agent_entrypoint,
            client_name=self.launch_context.client_name,
        )

    def _last_user_message(self) -> LLMMessage | None:
        return AgentLoop._last_user_message_from(self.messages)

    @staticmethod
    def _last_user_message_from(messages: Sequence[LLMMessage]) -> LLMMessage | None:
        return next(
            (m for m in reversed(messages) if m.role == Role.user and not m.injected),
            None,
        )

    def set_max_turns(self, max_turns: int) -> None:
        self._max_turns = max_turns
        self._setup_middleware()

    def set_max_tokens(self, max_tokens: int) -> None:
        self._max_tokens = max_tokens

    def _setup_middleware(self) -> None:
        """Configure middleware pipeline for this conversation."""
        self.middleware_pipeline.clear()

        if self._max_turns is not None:
            self.middleware_pipeline.add(TurnLimitMiddleware(self._max_turns))

        if self._max_price is not None:
            self.middleware_pipeline.add(PriceLimitMiddleware(self._max_price))

        if self._max_session_tokens is not None:
            self.middleware_pipeline.add(TokenLimitMiddleware(self._max_session_tokens))

        self.middleware_pipeline.add(AutoCompactMiddleware())
        if self.config.context_warnings:
            self.middleware_pipeline.add(ContextWarningMiddleware(0.5))

        self.middleware_pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: self.agent_profile,
                BuiltinAgentName.PLAN,
                lambda: make_plan_agent_reminder(
                    self._plan_session.plan_file_path_str,
                    has_ask_user_question="ask_user_question"
                    in self.tool_manager.available_tools,
                    has_exit_plan_mode="exit_plan_mode"
                    in self.tool_manager.available_tools,
                ),
                PLAN_AGENT_EXIT,
            )
        )
        self.middleware_pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: self.agent_profile,
                BuiltinAgentName.CHAT,
                CHAT_AGENT_REMINDER,
                CHAT_AGENT_EXIT,
            )
        )

    async def _handle_middleware_result(
        self, result: MiddlewareResult
    ) -> AsyncGenerator[BaseEvent]:
        match result.action:
            case MiddlewareAction.STOP:
                yield AssistantEvent(
                    content=f"<{VIBE_STOP_EVENT_TAG}>{result.reason}</{VIBE_STOP_EVENT_TAG}>",
                    stopped_by_middleware=True,
                )

            case MiddlewareAction.INJECT_MESSAGE:
                if result.message:
                    injected_message = LLMMessage(
                        role=Role.user, content=result.message, injected=True
                    )
                    self.messages.append(injected_message)

            case MiddlewareAction.COMPACT:
                async for event in self._run_compaction():
                    yield event

            case MiddlewareAction.CONTINUE:
                pass

    async def _run_compaction(self) -> AsyncGenerator[BaseEvent]:
        # Auto/reactive compaction: emit boundary events, compact, report status.
        old_tokens = self.stats.context_tokens
        threshold = self.config.get_active_model().auto_compact_threshold
        old_session_id = self.session_id
        old_parent_session_id = self.parent_session_id
        tool_call_id = str(uuid4())

        yield CompactStartEvent(
            tool_call_id=tool_call_id,
            current_context_tokens=old_tokens,
            threshold=threshold,
        )

        compact_status: Literal["success", "failure", "cancelled"] = "success"
        try:
            summary = await self.compact()
        except asyncio.CancelledError:
            compact_status = "cancelled"
            raise
        except Exception:
            compact_status = "failure"
            raise
        finally:
            self.telemetry_client.send_auto_compact_triggered(
                nb_context_tokens_before=old_tokens,
                auto_compact_threshold=threshold,
                status=compact_status,
                session_id=old_session_id,
                parent_session_id=old_parent_session_id,
            )

        yield CompactEndEvent(
            tool_call_id=tool_call_id,
            summary_length=len(summary),
            old_session_id=old_session_id,
            new_session_id=self.session_id,
        )

    @property
    def user_plan(self) -> str | None:
        return self._user_plan

    def set_user_plan(self, user_plan: str | None) -> None:
        self._user_plan = user_plan

    def _should_self_heal(self) -> bool:
        # Recover from an overflow at most once per turn; strict mode surfaces it.
        return (
            not self._reactive_recovery_used
            and not self.config.raise_on_compaction_failure
        )

    def _get_context(self) -> ConversationContext:
        return ConversationContext(
            messages=self.messages, stats=self.stats, config=self.config
        )

    def _build_backend_metadata(
        self, call_type: TelemetryCallType | None = None
    ) -> TelemetryRequestMetadata:
        return build_request_metadata(
            launch_context=self.launch_context,
            session_id=self.session_id,
            parent_session_id=self.parent_session_id,
            call_type=(
                call_type
                if call_type is not None
                else ("main_call" if self._is_user_prompt_call else "secondary_call")
            ),
            message_id=self._current_user_message_id,
            user_plan=self.user_plan,
        )

    def _get_extra_headers(
        self, provider: ProviderConfig | None = None
    ) -> dict[str, str]:
        provider = self.config.get_active_provider() if provider is None else provider
        headers: dict[str, str] = {**provider.extra_headers}
        headers["user-agent"] = get_user_agent(provider.backend)
        headers["x-affinity"] = self.session_id
        return headers

    async def _open_user_turn(
        self,
        user_msg: str,
        *,
        client_message_id: str | None = None,
        auto_title: str | None = None,
        images: list[ImageAttachment] | None = None,
        user_display_content: UserDisplayContentMetadata | None = None,
    ) -> AsyncGenerator[BaseEvent]:
        user_message = LLMMessage(
            role=Role.user,
            content=user_msg,
            message_id=client_message_id,
            images=images or None,
            user_display_content=user_display_content,
        )
        self.messages.append(user_message)
        self.stats.steps += 1
        self._current_user_message_id = user_message.message_id

        if user_message.message_id is None:
            raise AgentLoopError("User message must have a message_id")

        yield UserMessageEvent(content=user_msg, message_id=user_message.message_id)

        async for event in self._inject_invoked_skill(user_msg):
            yield event

        async for event in self._inject_mentioned_files(user_msg):
            yield event

        if auto_title is not None and self.session_logger.set_initial_auto_title(
            auto_title
        ):
            yield SessionTitleUpdatedEvent(title=auto_title)

        if self._hooks_manager:
            self._hooks_manager.reset_retry_count()

    async def _conversation_loop(
        self,
        user_msg: str,
        client_message_id: str | None = None,
        *,
        auto_title: str | None = None,
        images: list[ImageAttachment] | None = None,
        user_display_content: UserDisplayContentMetadata | None = None,
    ) -> AsyncGenerator[BaseEvent]:
        async for event in self._open_user_turn(
            user_msg,
            client_message_id=client_message_id,
            auto_title=auto_title,
            images=images,
            user_display_content=user_display_content,
        ):
            yield event

        try:
            should_break_loop = False
            first_llm_turn = True
            self._reactive_recovery_used = False
            while not should_break_loop:
                self._is_user_prompt_call = False
                result = await self.middleware_pipeline.run_before_turn(
                    self._get_context()
                )
                async for event in self._handle_middleware_result(result):
                    yield event

                if result.action == MiddlewareAction.STOP:
                    return

                user_cancelled = False
                self._is_user_prompt_call = first_llm_turn
                try:
                    async for event in self._perform_llm_turn():
                        if is_user_cancellation_event(event):
                            user_cancelled = True
                        yield event
                except ContextTooLongError:
                    if not self._should_self_heal():
                        raise
                    self._reactive_recovery_used = True
                    async for event in self._run_compaction():
                        yield event
                    continue  # retry the turn — still the user's first response
                # A turn ran to completion: count it against the turn budget (so
                # an overflow-and-retry never does) and mark later turns as
                # follow-ups.
                self.stats.steps += 1
                first_llm_turn = False
                # Per-turn save so the on-disk log stays fresh; after the
                # inner loop so pre_tool rewrites land in the snapshot.
                await self._save_messages()
                self._is_user_prompt_call = False

                if self._pending_clear_context:
                    async for event in self._clear_context_after_plan_accept():
                        yield event
                    should_break_loop = False
                    continue

                last_message = self.messages[-1]
                drained = self._drain_pending_injections()
                should_break_loop = last_message.role != Role.tool and not drained

                if user_cancelled:
                    return

                if should_break_loop:
                    retry_msg, hook_events = await self._dispatch_post_turn_hooks()
                    for hook_event in hook_events:
                        yield hook_event
                    should_break_loop = self._queue_post_turn_retry(retry_msg)

        finally:
            await self._save_messages()

    def _queue_post_turn_retry(self, retry_msg: LLMMessage | None) -> bool:
        # Returns whether the loop should still break (no retry queued).
        if retry_msg is None:
            return True
        self.messages.append(retry_msg)
        return False

    def _skill_already_loaded(self, name: str) -> bool:
        marker = skill_content_marker(name)
        return any(
            m.role == Role.tool and m.name == "skill" and marker in (m.content or "")
            for m in self.messages
        )

    async def _inject_invoked_skill(
        self, user_msg: str
    ) -> AsyncGenerator[BaseEvent, None]:
        parsed = self.skill_manager.parse_skill_command(user_msg)
        if parsed is None:
            return
        skill_info = self.skill_manager.get_skill(parsed.name)
        if skill_info is None:
            return

        result = select_skill_result(
            skill_info, already_loaded=self._skill_already_loaded(parsed.name)
        )
        call_id = str(uuid4())
        tool_class = self.tool_manager.available_tools.get("skill", SkillTool)

        self.messages.append(
            LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        index=0,
                        function=FunctionCall(
                            name="skill", arguments=json.dumps({"name": parsed.name})
                        ),
                    )
                ],
            )
        )
        result_text = "\n".join(f"{k}: {v}" for k, v in result.model_dump().items())
        self.messages.append(
            LLMMessage(
                role=Role.tool, tool_call_id=call_id, name="skill", content=result_text
            )
        )

        yield ToolCallEvent(
            tool_call_id=call_id,
            tool_call_index=0,
            tool_name="skill",
            tool_class=tool_class,
            args=SkillArgs(name=parsed.name),
        )
        yield ToolResultEvent(
            tool_name="skill",
            tool_class=tool_class,
            result=result,
            tool_call_id=call_id,
        )

    async def _inject_mentioned_files(
        self, user_msg: str
    ) -> AsyncGenerator[BaseEvent, None]:
        payload = build_path_prompt_payload(user_msg)
        file_resources = [r for r in payload.resources if r.kind == "file"]
        if not file_resources:
            return
        try:
            tool_instance = self.tool_manager.get("read_file")
        except NoSuchToolError:
            return
        tool_class = type(tool_instance)

        for resource in file_resources:
            file_path = str(resource.path)
            call_id = str(uuid4())
            self.messages.append(
                LLMMessage(
                    role=Role.assistant,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id=call_id,
                            index=0,
                            function=FunctionCall(
                                name="read_file",
                                arguments=json.dumps({"file_path": file_path}),
                            ),
                        )
                    ],
                )
            )
            tool_call = ResolvedToolCall(
                tool_name="read_file",
                tool_class=tool_class,
                validated_args=ReadFileArgs(file_path=file_path),
                call_id=call_id,
            )
            yield ToolCallEvent(
                tool_call_id=call_id,
                tool_call_index=0,
                tool_name="read_file",
                tool_class=tool_class,
                args=ReadFileArgs(file_path=file_path),
            )
            async for event in self._process_one_tool_call(tool_call):
                yield event

    def _handle_plan_review_ended(self) -> None:
        if not self._plan_session.has_content_changed():
            return None

        content = self._plan_session.read()
        if content is None:
            return None

        msg = LLMMessage(
            role=Role.user,
            content=(
                f"<{VIBE_WARNING_TAG}>The user has manually updated the plan file. "
                f"Here is the updated version -- use this as the source of truth "
                f"for implementation:\n\n{content}</{VIBE_WARNING_TAG}>"
            ),
            injected=True,
        )
        self._pending_injected_messages.append(msg)

    def _handle_session_plan_events(self, event: BaseEvent) -> BaseEvent | None:
        if isinstance(event, ToolCallEvent) and event.tool_name == "exit_plan_mode":
            self._plan_session.snapshot_content_hash()
            return PlanReviewRequestedEvent(file_path=self._plan_session.plan_file_path)

        if isinstance(event, ToolResultEvent) and event.tool_name == "exit_plan_mode":
            self._handle_plan_review_ended()
            return PlanReviewEndedEvent()

        return None

    async def _perform_llm_turn(self) -> AsyncGenerator[BaseEvent, None]:
        if self.enable_streaming:
            async for event in self._stream_assistant_events():
                yield event
        else:
            assistant_event = await self._get_assistant_event()
            if assistant_event.content:
                yield assistant_event

        last_message = self.messages[-1]

        parsed = self.format_handler.parse_message(last_message)
        resolved = self.format_handler.resolve_tool_calls(parsed, self.tool_manager)

        if not resolved.tool_calls and not resolved.failed_calls:
            return

        profile_before = self.agent_profile.name
        async for event in self._handle_tool_calls(resolved):
            yield event

            if session_plan_event := self._handle_session_plan_events(event):
                yield session_plan_event

        if self.agent_profile.name != profile_before:
            yield AgentProfileChangedEvent(agent_name=self.agent_profile.name)

    def _build_tool_call_events(
        self, tool_calls: list[ToolCall] | None, emitted_ids: set[str]
    ) -> Generator[ToolCallEvent, None, None]:
        for tc in tool_calls or []:
            if tc.id is None or not tc.function.name:
                continue
            if tc.id in emitted_ids:
                continue

            tool_class = self.tool_manager.available_tools.get(tc.function.name)
            if tool_class is None:
                continue

            yield ToolCallEvent(
                tool_call_id=tc.id,
                tool_call_index=tc.index,
                tool_name=tc.function.name,
                tool_class=tool_class,
            )

    async def _stream_assistant_events(
        self,
    ) -> AsyncGenerator[AssistantEvent | ReasoningEvent | ToolCallEvent]:
        message_id: str | None = None
        reasoning_message_id: str | None = None
        emitted_tool_call_ids = set[str]()

        async for chunk in self._chat_streaming():
            if message_id is None:
                message_id = chunk.message.message_id
            if reasoning_message_id is None:
                reasoning_message_id = chunk.message.reasoning_message_id

            for event in self._build_tool_call_events(
                chunk.message.tool_calls, emitted_tool_call_ids
            ):
                emitted_tool_call_ids.add(event.tool_call_id)
                yield event

            if chunk.message.reasoning_content:
                yield ReasoningEvent(
                    content=chunk.message.reasoning_content,
                    message_id=reasoning_message_id,
                )

            if chunk.message.content:
                yield AssistantEvent(
                    content=chunk.message.content, message_id=message_id
                )

    async def _get_assistant_event(self) -> AssistantEvent:
        llm_result = await self._chat()
        return AssistantEvent(
            content=llm_result.message.content or "",
            message_id=llm_result.message.message_id,
        )

    async def _handle_tool_calls(
        self, resolved: ResolvedMessage
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent | ToolStreamEvent | HookEvent]:
        async for event in self._emit_failed_tool_events(resolved.failed_calls):
            yield event
        if not resolved.tool_calls:
            return

        for tool_call in resolved.tool_calls:
            yield ToolCallEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                args=tool_call.validated_args,
                tool_call_id=tool_call.call_id,
            )

        async for event in self._run_tools_concurrently(resolved.tool_calls):
            yield event

    async def _emit_failed_tool_events(
        self, failed_calls: list[FailedToolCall]
    ) -> AsyncGenerator[ToolResultEvent]:
        for failed in failed_calls:
            error_msg = f"<{TOOL_ERROR_TAG}>{failed.tool_name}: {failed.error}</{TOOL_ERROR_TAG}>"
            yield ToolResultEvent(
                tool_name=failed.tool_name,
                tool_class=None,
                error=error_msg,
                tool_call_id=failed.call_id,
            )
            self.stats.tool_calls_failed += 1
            self.messages.append(
                self.format_handler.create_failed_tool_response_message(
                    failed, error_msg
                )
            )

    async def _run_tools_concurrently(
        self, tool_calls: list[ResolvedToolCall]
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent | ToolStreamEvent | HookEvent]:
        """Execute multiple tool calls concurrently, yielding events as they arrive."""
        queue: asyncio.Queue[
            ToolCallEvent | ToolResultEvent | ToolStreamEvent | HookEvent | None
        ] = asyncio.Queue()

        tasks = [
            asyncio.create_task(self._execute_tool_to_queue(tc, queue))
            for tc in tool_calls
        ]

        async def _signal_when_all_done() -> None:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                await queue.put(None)

        monitor = asyncio.create_task(_signal_when_all_done())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        except GeneratorExit:
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise
        except asyncio.CancelledError:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            if not monitor.done():
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor

    async def _execute_tool_to_queue(
        self,
        tc: ResolvedToolCall,
        queue: asyncio.Queue[
            ToolCallEvent | ToolResultEvent | ToolStreamEvent | HookEvent | None
        ],
    ) -> None:
        """Run a single tool call, sending events to the queue."""
        async for event in self._process_one_tool_call(tc):
            await queue.put(event)

    async def _process_one_tool_call(
        self, tool_call: ResolvedToolCall
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent | HookEvent]:
        async with tool_span(
            tool_name=tool_call.tool_name,
            call_id=tool_call.call_id,
            arguments=tool_call.validated_args.model_dump_json(),
        ) as span:
            async for event in self._execute_tool_call(span, tool_call):
                yield event

    async def _execute_tool_call(
        self, span: trace.Span, tool_call: ResolvedToolCall
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent | HookEvent]:
        try:
            tool_instance = self.tool_manager.get(tool_call.tool_name)
        except Exception as exc:
            error_msg = f"Error getting tool '{tool_call.tool_name}': {exc}"
            yield self._tool_failure_event(tool_call, error_msg, span=span)
            return

        try:
            tool_input = self._serialize_tool_input(tool_call)
        except Exception as exc:
            error_msg = (
                f"<{TOOL_ERROR_TAG}>Failed to serialize tool input for "
                f"'{tool_call.tool_name}': {exc}</{TOOL_ERROR_TAG}>"
            )
            self.stats.tool_calls_failed += 1
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                error=error_msg,
                tool_call_id=tool_call.call_id,
            )
            self._handle_tool_response(tool_call, error_msg, "failure", span=span)
            return

        events, resolution = await self._run_pre_tool_pipeline(
            tool_call, tool_input, span=span
        )
        for ev in events:
            yield ev
        if resolution.denial_event is not None:
            yield resolution.denial_event
            return
        tool_call = resolution.tool_call
        tool_input = resolution.tool_input

        decision: ToolDecision | None = None
        tool_started = False
        try:
            decision = await self._should_execute_tool(
                tool_instance, tool_call.validated_args, tool_call.call_id
            )

            if decision.verdict == ToolExecutionResponse.SKIP:
                async for ev in self._handle_tool_skip(tool_call, decision, span=span):
                    yield ev
                return

            tool_started = True
            async for ev in self._invoke_tool(
                tool_call, tool_instance, tool_input, decision, span=span
            ):
                yield ev

        except asyncio.CancelledError:
            cancel = str(
                get_user_cancellation_message(CancellationReason.TOOL_INTERRUPTED)
            )
            self.stats.tool_calls_failed += 1
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                error=cancel,
                cancelled=True,
                tool_call_id=tool_call.call_id,
            )
            async for ev in self._finalize_cancelled_tool(
                tool_call,
                tool_input,
                decision,
                cancel,
                span=span,
                tool_started=tool_started,
            ):
                yield ev
            raise

        except Exception as exc:
            error_msg = f"<{TOOL_ERROR_TAG}>{tool_instance.get_name()} failed: {exc}</{TOOL_ERROR_TAG}>"
            if isinstance(exc, ToolPermissionError):
                self.stats.tool_calls_agreed -= 1
                self.stats.tool_calls_rejected += 1
            else:
                self.stats.tool_calls_failed += 1
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                error=error_msg,
                tool_call_id=tool_call.call_id,
            )
            async for ev in self._run_post_tool_and_finalize(
                tool_call,
                tool_input=tool_input,
                tool_status="failure",
                response_status="failure",
                decision=decision,
                span=span,
                tool_error=str(exc),
                initial_text=error_msg,
            ):
                yield ev

    async def _invoke_tool(
        self,
        tool_call: ResolvedToolCall,
        tool_instance: BaseTool,
        tool_input: dict[str, Any],
        decision: ToolDecision,
        *,
        span: trace.Span,
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent | HookEvent]:
        self.stats.tool_calls_agreed += 1

        snapshot = await asyncio.to_thread(
            tool_instance.get_file_snapshot, tool_call.validated_args
        )
        if snapshot is not None:
            self.checkpoint_recorder.add_snapshot(snapshot)

        start_time = time.perf_counter()
        result_model = None
        async for item in tool_instance.invoke(
            ctx=InvokeContext(
                tool_call_id=tool_call.call_id,
                agent_manager=self.agent_manager,
                session_dir=self.session_logger.session_dir,
                launch_context=self.launch_context,
                approval_callback=self.approval_callback,
                user_input_callback=self.user_input_callback,
                sampling_callback=self._sampling_handler,
                plan_file_path=self._plan_session.plan_file_path,
                switch_agent_callback=self.switch_agent,
                request_clear_context_callback=self._request_clear_context,
                skill_manager=self.skill_manager,
                is_skill_loaded=self._skill_already_loaded,
                scratchpad_dir=self.scratchpad_dir,
                permission_store=self._permission_store,
                hook_config_result=self._hook_config_result,
                session_id=self.session_id,
                mcp_pool=self._mcp_pool,
            ),
            **tool_call.args_dict,
        ):
            if isinstance(item, ToolStreamEvent):
                yield item
            else:
                result_model = item

        duration = time.perf_counter() - start_time
        if result_model is None:
            raise ToolError("Tool did not yield a result")

        result_dict = result_model.model_dump()
        text = "\n".join(f"{k}: {v}" for k, v in result_dict.items())
        extra = tool_instance.get_result_extra(result_model)
        if extra:
            text += "\n\n" + extra

        result_cancelled = (
            isinstance(result_model, CancellableToolResult) and result_model.cancelled
        )
        yield ToolResultEvent(
            tool_name=tool_call.tool_name,
            tool_class=tool_call.tool_class,
            result=result_model,
            cancelled=result_cancelled,
            duration=duration,
            tool_call_id=tool_call.call_id,
        )
        async for ev in self._run_post_tool_and_finalize(
            tool_call,
            tool_input=tool_input,
            tool_status="cancelled" if result_cancelled else "success",
            response_status="success",
            decision=decision,
            span=span,
            tool_output=result_dict,
            duration_ms=duration * 1000.0,
            initial_text=text,
        ):
            yield ev
        self.stats.tool_calls_succeeded += 1

    async def _should_execute_tool(
        self, tool: BaseTool, args: BaseModel, tool_call_id: str
    ) -> ToolDecision:
        if self.bypass_tool_permissions:
            return ToolDecision(
                verdict=ToolExecutionResponse.EXECUTE,
                approval_type=ToolPermission.ALWAYS,
            )

        async with self._permission_store.lock:
            tool_name = tool.get_name()
            ctx = tool.resolve_permission(args)

            if ctx is None:
                config_perm = self.tool_manager.get_tool_config(tool_name).permission
                ctx = PermissionContext(permission=config_perm)

            match ctx.permission:
                case ToolPermission.ALWAYS:
                    return ToolDecision(
                        verdict=ToolExecutionResponse.EXECUTE,
                        approval_type=ToolPermission.ALWAYS,
                    )
                case ToolPermission.NEVER:
                    return ToolDecision(
                        verdict=ToolExecutionResponse.SKIP,
                        approval_type=ToolPermission.NEVER,
                        feedback=ctx.reason
                        or f"Tool '{tool_name}' is permanently disabled",
                    )
                case _:
                    uncovered = [
                        rp
                        for rp in ctx.required_permissions
                        if not self._permission_store.covers(tool_name, rp)
                    ]
                    if ctx.required_permissions and not uncovered:
                        return ToolDecision(
                            verdict=ToolExecutionResponse.EXECUTE,
                            approval_type=ToolPermission.ALWAYS,
                        )
                    return await self._ask_approval(
                        tool_name, args, tool_call_id, uncovered
                    )

    async def _ask_approval(
        self,
        tool_name: str,
        args: BaseModel,
        tool_call_id: str,
        required_permissions: list[RequiredPermission],
    ) -> ToolDecision:
        if not self.approval_callback:
            return ToolDecision(
                verdict=ToolExecutionResponse.SKIP,
                approval_type=ToolPermission.ASK,
                feedback="Tool execution not permitted.",
            )
        response, feedback = await self.approval_callback(
            tool_name, args, tool_call_id, required_permissions
        )

        match response:
            case ApprovalResponse.YES:
                verdict = ToolExecutionResponse.EXECUTE
            case _:
                verdict = ToolExecutionResponse.SKIP

        return ToolDecision(
            verdict=verdict, approval_type=ToolPermission.ASK, feedback=feedback
        )

    def _handle_tool_response(
        self,
        tool_call: ResolvedToolCall,
        text: str,
        status: Literal["success", "failure", "skipped"],
        decision: ToolDecision | None = None,
        result: dict[str, Any] | None = None,
        span: trace.Span | None = None,
    ) -> None:
        self.messages.append(
            LLMMessage.model_validate(
                self.format_handler.create_tool_response_message(tool_call, text)
            )
        )

        if span is not None:
            set_tool_result(span, text)
        self.telemetry_client.send_tool_call_finished(
            tool_call=tool_call,
            agent_profile_name=self.agent_profile.name,
            model=self.config.active_model,
            status=status,
            decision=decision,
            result=result,
            message_id=self._current_user_message_id,
        )

    def _tool_failure_event(
        self,
        tool_call: ResolvedToolCall,
        error_msg: str,
        decision: ToolDecision | None = None,
        cancelled: bool = False,
        span: trace.Span | None = None,
    ) -> ToolResultEvent:
        """Create a ToolResultEvent for a failed tool and record the failure."""
        self._handle_tool_response(tool_call, error_msg, "failure", decision, span=span)
        return ToolResultEvent(
            tool_name=tool_call.tool_name,
            tool_class=tool_call.tool_class,
            error=error_msg,
            cancelled=cancelled,
            tool_call_id=tool_call.call_id,
        )

    def _messages_for_backend(
        self, messages: Sequence[LLMMessage], active_model: ModelConfig
    ) -> Sequence[LLMMessage]:
        if active_model.supports_images:
            return messages
        if not any(m.images for m in messages):
            return messages
        return [
            m.model_copy(update={"images": None}) if m.images else m for m in messages
        ]

    def count_history_images_unsupported_by_active_model(self) -> int:
        try:
            active_model = self.config.get_active_model()
        except ValueError:
            return 0
        if active_model.supports_images:
            return 0
        return sum(1 for m in self.messages if m.images)

    async def _complete(
        self,
        *,
        model: ModelConfig,
        messages: Sequence[LLMMessage],
        tools: list[AvailableTool] | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        call_type: TelemetryCallType | None,
    ) -> LLMChunk:
        """Make one accounted, non-streaming model call.

        Sends request telemetry, calls the backend, updates stats, and maps
        backend errors. Does NOT append to self.messages or raise on refusal —
        those are the caller's concern. This is the single path every
        non-streaming call (including compaction) goes through, so usage
        accounting can never be skipped.
        """
        provider = self.config.get_provider_for_model(model)
        backend_metadata = self._build_backend_metadata(call_type)

        last_user_message = next(
            (m for m in reversed(messages) if m.role == Role.user and not m.injected),
            None,
        )
        self.telemetry_client.send_request_sent(
            model=model.alias,
            nb_context_chars=sum(len(m.content or "") for m in messages),
            nb_context_messages=len(messages),
            nb_prompt_chars=len(last_user_message.content or "")
            if last_user_message
            else 0,
            call_type=backend_metadata.call_type,
            message_id=backend_metadata.message_id,
            attachment_counts=build_attachment_counts(
                last_user_message, supports_images=model.supports_images
            ),
        )

        try:
            start_time = time.perf_counter()
            result = await self.backend.complete(
                model=model,
                messages=self._messages_for_backend(messages, model),
                temperature=model.temperature,
                tools=tools,
                tool_choice=tool_choice,
                extra_headers=self._get_extra_headers(provider),
                max_tokens=self._max_tokens,
                metadata=backend_metadata.model_dump(exclude_none=True),
            )
            end_time = time.perf_counter()

            if result.usage is None:
                raise AgentLoopLLMResponseError(
                    "Usage data missing in non-streaming completion response"
                )
            self._update_stats(usage=result.usage, time_seconds=end_time - start_time)

            if result.correlation_id:
                self.telemetry_client.last_correlation_id = result.correlation_id

            processed_message = self.format_handler.process_api_response_message(
                result.message
            )
            return LLMChunk(
                message=processed_message, usage=result.usage, stop=result.stop
            )

        except Exception as e:
            if isinstance(e, RefusalError):
                raise
            if _should_raise_rate_limit_error(e):
                raise RateLimitError(provider.name, model.name) from e
            if _is_context_too_long_error(e):
                raise ContextTooLongError(provider.name, model.name) from e
            if _is_response_too_long_error(e):
                raise ResponseTooLongError(provider.name, model.name) from e
            if _is_non_retryable_error(e):
                raise

            raise RuntimeError(
                f"API error from {provider.name} (model: {model.name}): {e}"
            ) from e

    async def _chat(
        self,
        model_override: ModelConfig | None = None,
        *,
        call_type: TelemetryCallType | None = None,
    ) -> LLMChunk:
        active_model = model_override or self.config.get_active_model()
        result = await self._complete(
            model=active_model,
            messages=self.messages,
            tools=self.format_handler.get_available_tools(self.tool_manager),
            tool_choice=self.format_handler.get_tool_choice(),
            call_type=call_type,
        )
        self.messages.append(result.message)
        if result.stop and result.stop.is_refusal:
            provider = self.config.get_provider_for_model(active_model)
            raise _refusal_error(provider.name, active_model.name, result)
        return result

    async def _chat_streaming(self) -> AsyncGenerator[LLMChunk]:
        active_model = self.config.get_active_model()
        provider = self.config.get_active_provider()
        backend_metadata = self._build_backend_metadata()

        available_tools = self.format_handler.get_available_tools(self.tool_manager)
        tool_choice = self.format_handler.get_tool_choice()

        last_user_message = self._last_user_message()
        self.telemetry_client.send_request_sent(
            model=active_model.alias,
            nb_context_chars=sum(len(m.content or "") for m in self.messages),
            nb_context_messages=len(self.messages),
            nb_prompt_chars=len(last_user_message.content or "")
            if last_user_message
            else 0,
            call_type=backend_metadata.call_type,
            message_id=backend_metadata.message_id,
            attachment_counts=build_attachment_counts(
                last_user_message, supports_images=active_model.supports_images
            ),
        )

        try:
            start_time = time.perf_counter()
            usage = LLMUsage()
            chunk_agg: LLMChunk | None = None
            async for chunk in self.backend.complete_streaming(
                model=active_model,
                messages=self._messages_for_backend(self.messages, active_model),
                temperature=active_model.temperature,
                tools=available_tools,
                tool_choice=tool_choice,
                extra_headers=self._get_extra_headers(),
                max_tokens=self._max_tokens,
                metadata=backend_metadata.model_dump(exclude_none=True),
            ):
                if chunk.correlation_id:
                    self.telemetry_client.last_correlation_id = chunk.correlation_id
                processed_message = self.format_handler.process_api_response_message(
                    chunk.message
                )
                processed_chunk = LLMChunk(
                    message=processed_message, usage=chunk.usage, stop=chunk.stop
                )
                chunk_agg = (
                    processed_chunk
                    if chunk_agg is None
                    else chunk_agg + processed_chunk
                )
                usage += chunk.usage or LLMUsage()
                yield processed_chunk
            end_time = time.perf_counter()

            if chunk_agg is None or chunk_agg.usage is None:
                raise AgentLoopLLMResponseError(
                    "Usage data missing in final chunk of streamed completion"
                )
            self._update_stats(usage=usage, time_seconds=end_time - start_time)

            self.messages.append(chunk_agg.message)
            if chunk_agg.stop and chunk_agg.stop.is_refusal:
                raise _refusal_error(provider.name, active_model.name, chunk_agg)

        except Exception as e:
            if isinstance(e, RefusalError):
                raise
            if _should_raise_rate_limit_error(e):
                raise RateLimitError(provider.name, active_model.name) from e
            if _is_context_too_long_error(e):
                raise ContextTooLongError(provider.name, active_model.name) from e
            if _is_response_too_long_error(e):
                raise ResponseTooLongError(provider.name, active_model.name) from e
            if _is_non_retryable_error(e):
                raise

            raise RuntimeError(
                f"API error from {provider.name} (model: {active_model.name}): {e}"
            ) from e

    def _update_stats(self, usage: LLMUsage, time_seconds: float) -> None:
        self.stats.last_turn_duration = time_seconds
        self.stats.last_turn_prompt_tokens = usage.prompt_tokens
        self.stats.last_turn_completion_tokens = usage.completion_tokens
        self.stats.session_prompt_tokens += usage.prompt_tokens
        self.stats.session_completion_tokens += usage.completion_tokens
        self.stats.context_tokens = usage.prompt_tokens + usage.completion_tokens
        if time_seconds > 0 and usage.completion_tokens > 0:
            self.stats.tokens_per_second = usage.completion_tokens / time_seconds

    def _clean_message_history(self) -> None:
        ACCEPTABLE_HISTORY_SIZE = 2
        if len(self.messages) < ACCEPTABLE_HISTORY_SIZE:
            return
        self._fill_missing_tool_responses()

    def _fill_missing_tool_responses(self) -> None:
        i = 1
        while i < len(self.messages):  # noqa: PLR1702
            msg = self.messages[i]

            if msg.role == "assistant" and msg.tool_calls:
                expected_responses = len(msg.tool_calls)

                if expected_responses > 0:
                    responded_ids: set[str] = set()
                    j = i + 1
                    while j < len(self.messages) and self.messages[j].role == "tool":
                        tool_call_id = self.messages[j].tool_call_id
                        if tool_call_id is not None:
                            responded_ids.add(tool_call_id)
                        j += 1

                    if len(responded_ids) < expected_responses:
                        insertion_point = j

                        for tool_call_data in msg.tool_calls:
                            if (tool_call_data.id or "") in responded_ids:
                                continue

                            empty_response = LLMMessage(
                                role=Role.tool,
                                tool_call_id=tool_call_data.id or "",
                                name=(
                                    (tool_call_data.function.name or "")
                                    if tool_call_data.function
                                    else ""
                                ),
                                content=str(
                                    get_user_cancellation_message(
                                        CancellationReason.TOOL_NO_RESPONSE
                                    )
                                ),
                            )

                            self.messages.insert(insertion_point, empty_response)
                            insertion_point += 1

                    i = i + 1 + expected_responses
                    continue

            i += 1

    async def _reset_session(self, keep_parent: bool = True) -> None:
        old_session_id = self.session_id
        self.emit_session_closed_telemetry()
        suffix = extract_suffix(self.session_id)
        self.session_id = generate_session_id(suffix=suffix)
        parent_session_id = old_session_id if keep_parent else None
        self.parent_session_id = parent_session_id
        self.session_logger.reset_session(
            self.session_id, parent_session_id=parent_session_id
        )
        await self.initialize_experiments()
        self.emit_new_session_telemetry()

    async def fork(self, message_id: str | None = None) -> AgentLoop:
        messages = self._messages_for_fork(message_id)
        forked = AgentLoop(
            # Temporary (PR5): deepcopy is safe for the legacy adapter (config only); PR6 must revisit for the real orchestrator (locks/subscriptions).
            config_orchestrator=copy.deepcopy(self._config_orchestrator),
            agent_name=self.agent_profile.name,
            max_turns=self._max_turns,
            max_price=self._max_price,
            max_tokens=self._max_tokens,
            max_session_tokens=self._max_session_tokens,
            enable_streaming=self.enable_streaming,
            launch_context=self.launch_context,
            defer_heavy_init=True,
            hook_config_result=self._hook_config_result,
            cache_store=self.cache_store,
        )
        forked.session_id = generate_session_id(suffix=extract_suffix(self.session_id))
        forked.parent_session_id = self.session_id
        forked.session_logger.reset_session(
            forked.session_id, parent_session_id=self.session_id
        )
        forked.messages.extend(messages)
        await forked.session_logger.save_interaction(
            forked.messages,
            forked.stats,
            forked.base_config,
            forked.tool_manager,
            forked.agent_profile,
        )
        return forked

    def _messages_for_fork(self, message_id: str | None) -> list[LLMMessage]:
        source_messages = [m for m in self.messages if m.role != Role.system]
        if message_id is None:
            return [m.model_copy(deep=True) for m in source_messages]

        anchor_index = next(
            (i for i, m in enumerate(source_messages) if message_id == m.message_id),
            None,
        )
        if anchor_index is None:
            raise ValueError(f"Cannot fork from unknown message_id: {message_id}")

        if source_messages[anchor_index].role != Role.user:
            raise ValueError("Fork from message_id is only supported for user messages")

        next_turn_index = next(
            (
                i
                for i, m in enumerate(
                    source_messages[anchor_index + 1 :], start=anchor_index + 1
                )
                if m.role == Role.user
            ),
            len(source_messages),
        )
        return [m.model_copy(deep=True) for m in source_messages[:next_turn_index]]

    @requires_init
    async def clear_history(self) -> None:
        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self.base_config,
            self.tool_manager,
            self.agent_profile,
        )
        self.messages.reset(self.messages[:1])

        self.stats = AgentStats.create_fresh(self.stats)
        self.stats.trigger_listeners()

        try:
            active_model = self.config.get_active_model()
            self.stats.update_pricing(
                active_model.input_price, active_model.output_price
            )
        except ValueError:
            pass

        self.middleware_pipeline.reset()
        self.tool_manager.reset_all()
        await self._reset_session(keep_parent=False)

    @requires_init
    async def compact(self, extra_instructions: str = "") -> str:
        # Summary generation + envelope + session reset live in the manager; the
        # loop keeps the surrounding lifecycle (clean history, save, middleware).
        try:
            self._clean_message_history()
            await self._save_messages()
            summary = await self.compaction_manager.compact(extra_instructions)
            self.middleware_pipeline.reset(reset_reason=ResetReason.COMPACT)
            return summary
        except Exception:
            await self._save_messages()
            raise

    async def _request_clear_context(self) -> None:
        """Signal that the context should be cleared at the next turn boundary.

        The actual clear is deferred so the in-flight tool turn can finish
        appending its tool-result before ``self.messages`` is wiped.
        """
        self._pending_clear_context = True

    async def _clear_context_after_plan_accept(self) -> AsyncGenerator[BaseEvent, None]:
        """Clear the conversation after a plan accept, then re-seed the plan.

        Runs at a turn boundary (never mid-tool) so the tool-result message has
        already landed before ``self.messages`` is reset. The approved plan is
        re-injected as a system-reminder so the implementing agent keeps going,
        and emitted in a ``ContextClearedEvent`` with its path so the UI can keep
        the plan on screen.
        """
        self._pending_clear_context = False
        self._pending_injected_messages.clear()
        plan_content = self._plan_session.read()
        await self.clear_history()
        if not plan_content:
            yield ContextClearedEvent(plan_file_path=None)
            return
        self.messages.append(
            LLMMessage(
                role=Role.user,
                content=(
                    f"<{VIBE_WARNING_TAG}>The conversation context was cleared "
                    f"after the plan was approved. Implement the approved plan "
                    f"below -- it is the source of truth:\n\n{plan_content}"
                    f"</{VIBE_WARNING_TAG}>"
                ),
                injected=True,
            )
        )
        yield ContextClearedEvent(plan_file_path=self._plan_session.plan_file_path)

    @requires_init
    async def switch_agent(self, agent_name: str) -> None:
        if agent_name == self.agent_profile.name:
            return
        await self.reload_with_initial_messages(
            reset_middleware=False, switch_to_agent=agent_name
        )

    @requires_init
    async def reload_with_initial_messages(
        self,
        max_turns: int | None = None,
        max_price: float | None = None,
        reset_middleware: bool = True,
        switch_to_agent: str | None = None,
    ) -> None:
        self._reload_generation += 1
        generation = self._reload_generation

        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self.base_config,
            self.tool_manager,
            self.agent_profile,
        )

        # A newer reload superseded us while we were saving; don't mutate state.
        if generation != self._reload_generation:
            return

        # Cheap on-loop setup; warm the config cache so the worker thread only reads it.
        # Callers mutate the orchestrator (reload / set_field) before reinit, so we just
        # re-read base_config and re-invalidate.
        self._apply_forced_bypass()
        self.agent_manager.invalidate_config()
        if max_turns is not None:
            self._max_turns = max_turns
        if max_price is not None:
            self._max_price = max_price
        self._ensure_remote_registries()
        _ = self.config

        # Resolve the config the reloaded objects should reflect. For an agent switch
        # this is the target agent's config, computed without mutating the active
        # profile -- so an in-flight turn keeps seeing the old, self-consistent config
        # until the synchronous commit flips profile and objects together.
        target_config = (
            self.agent_manager.preview_config(switch_to_agent)
            if switch_to_agent is not None
            else self.config
        )

        # Off-loop: skill discovery and system prompt I/O. reload() is awaited within a
        # turn, so that turn is suspended here -- nothing mutates the shared state this
        # reads, and the new objects are built locally before the commit below.
        prepared = await asyncio.to_thread(self._prepare_reload, target_config)

        # A newer reload superseded us; let it own the commit.
        if generation != self._reload_generation:
            return

        # Synchronous swap: no await, so an in-flight turn can't observe a partial
        # update. Keep it that way -- don't make it async or move it off-thread.
        self._commit_reload(prepared, reset_middleware, switch_to_agent)

    def _prepare_reload(self, target_config: AnyVibeConfig) -> _PreparedReload:
        config_source = _SwappableConfigSource(lambda: target_config)
        tool_manager = ToolManager(
            config_source.get,
            mcp_registry=self.mcp_registry,
            connector_registry=self.connector_registry,
            permission_getter=self._permission_store.get_tool_permission,
        )
        skill_manager = SkillManager(config_source.get)
        system_prompt = self._render_system_prompt(
            tool_manager, skill_manager, target_config
        )
        return _PreparedReload(
            backend=self.backend_factory(target_config),
            tool_manager=tool_manager,
            skill_manager=skill_manager,
            system_prompt=system_prompt,
            config_source=config_source,
        )

    def _commit_reload(
        self,
        prepared: _PreparedReload,
        reset_middleware: bool,
        switch_to_agent: str | None,
    ) -> None:
        if switch_to_agent is not None:
            self.agent_manager.switch_profile(switch_to_agent)
        # Now that the profile is live, the prepared managers should track the live
        # config so later refreshes (refresh_config) propagate to them.
        prepared.config_source.point_to(lambda: self.config)
        self.backend = prepared.backend
        self.tool_manager = prepared.tool_manager
        self.skill_manager = prepared.skill_manager
        self.messages.update_system_prompt(prepared.system_prompt)

        if len(self.messages) == 1:
            self.stats.reset_context_state()

        try:
            active_model = self.config.get_active_model()
            self.stats.update_pricing(
                active_model.input_price, active_model.output_price
            )
        except ValueError:
            pass

        if reset_middleware:
            self._setup_middleware()
