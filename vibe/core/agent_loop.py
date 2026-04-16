from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
import contextlib
import copy
from enum import StrEnum, auto
from http import HTTPStatus
import os
from pathlib import Path
import threading
from threading import Thread
import time
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from opentelemetry import trace
from pydantic import BaseModel

from vibe.cli.terminal_setup import detect_terminal
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentProfile, BuiltinAgentName
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.llm.backend.factory import BACKEND_FACTORY
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
    TurnLimitMiddleware,
    make_plan_agent_reminder,
)
from vibe.core.plan_session import PlanSession
from vibe.core.prompts import UtilityPrompt
from vibe.core.rewind import RewindManager
from vibe.core.session.session_logger import SessionLogger
from vibe.core.session.session_migration import migrate_sessions_entrypoint
from vibe.core.skills.manager import SkillManager
from vibe.core.system_prompt import get_universal_system_prompt
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.tools.base import (
    BaseTool,
    InvokeContext,
    ToolError,
    ToolPermission,
    ToolPermissionError,
)
from vibe.core.tools.connectors import ConnectorRegistry, connectors_enabled
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.mcp import MCPRegistry
from vibe.core.tools.mcp_sampling import MCPSamplingHandler
from vibe.core.tools.permissions import (
    ApprovedRule,
    PermissionContext,
    RequiredPermission,
)
from vibe.core.tools.utils import wildcard_match
from vibe.core.tracing import agent_span, set_tool_result, tool_span
from vibe.core.trusted_folders import has_agents_md_file
from vibe.core.types import (
    AgentProfileChangedEvent,
    AgentStats,
    ApprovalCallback,
    ApprovalResponse,
    AssistantEvent,
    BaseEvent,
    CompactEndEvent,
    CompactStartEvent,
    EntrypointMetadata,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    MessageList,
    RateLimitError,
    ReasoningEvent,
    Role,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserInputCallback,
    UserMessageEvent,
)
from vibe.core.utils import (
    CANCELLATION_TAG,
    TOOL_ERROR_TAG,
    VIBE_STOP_EVENT_TAG,
    CancellationReason,
    get_server_url_from_api_base,
    get_user_agent,
    get_user_cancellation_message,
    is_user_cancellation_event,
)

try:
    from vibe.core.teleport.teleport import TeleportService as _TeleportService

    _TELEPORT_AVAILABLE = True
except ImportError:
    _TELEPORT_AVAILABLE = False
    _TeleportService = None

if TYPE_CHECKING:
    from vibe.core.teleport.nuage import TeleportSession
    from vibe.core.teleport.teleport import TeleportService
    from vibe.core.teleport.types import TeleportPushResponseEvent, TeleportYieldEvent


class ToolExecutionResponse(StrEnum):
    SKIP = auto()
    EXECUTE = auto()


class ToolDecision(BaseModel):
    verdict: ToolExecutionResponse
    approval_type: ToolPermission
    feedback: str | None = None


class AgentLoopError(Exception):
    """Base exception for AgentLoop errors."""


class AgentLoopStateError(AgentLoopError):
    """Raised when agent loop is in an invalid state."""


class AgentLoopLLMResponseError(AgentLoopError):
    """Raised when LLM response is malformed or missing expected data."""


class TeleportError(AgentLoopError):
    """Raised when teleport to Vibe Nuage fails."""


def _should_raise_rate_limit_error(e: Exception) -> bool:
    return isinstance(e, BackendError) and e.status == HTTPStatus.TOO_MANY_REQUESTS


class AgentLoop:
    def __init__(
        self,
        config: VibeConfig,
        *,
        agent_name: str = BuiltinAgentName.DEFAULT,
        message_observer: Callable[[LLMMessage], None] | None = None,
        max_turns: int | None = None,
        max_price: float | None = None,
        backend: BackendLike | None = None,
        enable_streaming: bool = False,
        entrypoint_metadata: EntrypointMetadata | None = None,
        is_subagent: bool = False,
        defer_heavy_init: bool = False,
    ) -> None:
        self._base_config = config
        self._init_complete = threading.Event()
        self._init_error: Exception | None = None

        self.mcp_registry = MCPRegistry()
        self.connector_registry = self._create_connector_registry()
        self.agent_manager = AgentManager(
            lambda: self._base_config,
            initial_agent=agent_name,
            allow_subagent=is_subagent,
        )
        self.tool_manager = ToolManager(
            lambda: self.config,
            mcp_registry=self.mcp_registry,
            connector_registry=self.connector_registry,
            defer_mcp=defer_heavy_init,
        )
        self.skill_manager = SkillManager(lambda: self.config)
        self.message_observer = message_observer
        self._max_turns = max_turns
        self._max_price = max_price
        self._plan_session = PlanSession()

        self.format_handler = APIToolFormatHandler()

        self.backend_factory = lambda: backend or self._select_backend()
        self.backend = self.backend_factory()
        self._sampling_handler = MCPSamplingHandler(
            backend_getter=lambda: self.backend, config_getter=lambda: self.config
        )

        self.enable_streaming = enable_streaming
        self.middleware_pipeline = MiddlewarePipeline()
        self._setup_middleware()

        system_prompt = get_universal_system_prompt(
            self.tool_manager,
            self.config,
            self.skill_manager,
            self.agent_manager,
            include_git_status=not defer_heavy_init,
        )
        system_message = LLMMessage(role=Role.system, content=system_prompt)
        self.messages = MessageList(initial=[system_message], observer=message_observer)

        if not defer_heavy_init:
            self._init_complete.set()

        self.stats = AgentStats()
        self.approval_callback: ApprovalCallback | None = None
        self.user_input_callback: UserInputCallback | None = None
        self.entrypoint_metadata = entrypoint_metadata
        self.session_id = str(uuid4())

        try:
            active_model = config.get_active_model()
            self.stats.input_price_per_million = active_model.input_price
            self.stats.output_price_per_million = active_model.output_price
        except ValueError:
            pass

        self._current_user_message_id: str | None = None
        self._is_user_prompt_call: bool = False

        self._session_rules: list[ApprovedRule] = []
        self._approval_lock = asyncio.Lock()

        self.telemetry_client = TelemetryClient(
            config_getter=lambda: self.config, session_id_getter=lambda: self.session_id
        )
        self.session_logger = SessionLogger(config.session_logging, self.session_id)
        self.rewind_manager = RewindManager(
            messages=self.messages,
            save_messages=self._save_messages,
            reset_session=self._reset_session,
        )
        self._teleport_service: TeleportService | None = None

        thread = Thread(
            target=migrate_sessions_entrypoint,
            args=(config.session_logging,),
            daemon=True,
            name="migrate_sessions",
        )
        thread.start()

    def start_deferred_init(self) -> threading.Thread:
        """Spawn a daemon thread that finishes deferred heavy I/O.

        Returns the started thread so callers can join if needed.
        """
        thread = threading.Thread(
            target=self._complete_init, daemon=True, name="agent_loop_init"
        )
        thread.start()
        return thread

    @property
    def is_initialized(self) -> bool:
        """Whether deferred initialization has completed (successfully or not)."""
        return self._init_complete.is_set()

    def _complete_init(self) -> None:
        """Run deferred heavy I/O: MCP discovery, connectors, and git status.

        Intended to be called from a background thread when
        ``defer_heavy_init=True`` was passed to ``__init__``.
        """
        try:
            self.tool_manager.integrate_all(raise_on_mcp_failure=True)
            system_prompt = get_universal_system_prompt(
                self.tool_manager, self.config, self.skill_manager, self.agent_manager
            )
            self.messages.update_system_prompt(system_prompt)
        except Exception as exc:
            self._init_error = exc
        finally:
            self._init_complete.set()

    async def wait_for_init(self) -> None:
        """Await deferred initialization from an async context.

        If deferred init failed, all callers raise the stored error.

        A copy of the stored exception is raised each time so that concurrent
        callers do not share (and mutate) the same ``__traceback__`` object.
        """
        if self.is_initialized:
            if err := self._init_error:
                raise copy.copy(err).with_traceback(err.__traceback__)
            return
        await asyncio.to_thread(self._init_complete.wait)
        if err := self._init_error:
            raise copy.copy(err).with_traceback(err.__traceback__)

    @property
    def agent_profile(self) -> AgentProfile:
        return self.agent_manager.active_profile

    @property
    def base_config(self) -> VibeConfig:
        return self._base_config

    @property
    def config(self) -> VibeConfig:
        return self.agent_manager.config

    @property
    def auto_approve(self) -> bool:
        return self.config.auto_approve

    def refresh_config(self) -> None:
        self._base_config = VibeConfig.load()
        self.agent_manager.invalidate_config()

    def set_approval_callback(self, callback: ApprovalCallback) -> None:
        self.approval_callback = callback

    def set_user_input_callback(self, callback: UserInputCallback) -> None:
        self.user_input_callback = callback

    def set_tool_permission(
        self, tool_name: str, permission: ToolPermission, save_permanently: bool = False
    ) -> None:
        if save_permanently:
            VibeConfig.save_updates({
                "tools": {tool_name: {"permission": permission.value}}
            })

        if tool_name not in self.config.tools:
            self.config.tools[tool_name] = {}

        self.config.tools[tool_name]["permission"] = permission.value

    def _add_session_rule(self, rule: ApprovedRule) -> None:
        self._session_rules.append(rule)

    def _is_permission_covered(self, tool_name: str, rp: RequiredPermission) -> bool:
        return any(
            rule.tool_name == tool_name
            and rule.scope == rp.scope
            and wildcard_match(rp.invocation_pattern, rule.session_pattern)
            for rule in self._session_rules
        )

    def approve_always(
        self,
        tool_name: str,
        required_permissions: list[RequiredPermission] | None,
        save_permanently: bool = False,
    ) -> None:
        """Handle 'Allow Always' approval: add session rules or set tool-level permission."""
        if required_permissions:
            for rp in required_permissions:
                self._add_session_rule(
                    ApprovedRule(
                        tool_name=tool_name,
                        scope=rp.scope,
                        session_pattern=rp.session_pattern,
                    )
                )
        else:
            self.set_tool_permission(
                tool_name, ToolPermission.ALWAYS, save_permanently=save_permanently
            )

    def emit_new_session_telemetry(self) -> None:
        entrypoint = (
            self.entrypoint_metadata.agent_entrypoint
            if self.entrypoint_metadata
            else "unknown"
        )
        client_name = (
            self.entrypoint_metadata.client_name if self.entrypoint_metadata else None
        )
        client_version = (
            self.entrypoint_metadata.client_version
            if self.entrypoint_metadata
            else None
        )
        has_agents_md = has_agents_md_file(Path.cwd())
        nb_skills = len(self.skill_manager.available_skills)
        nb_mcp_servers = len(self.config.mcp_servers)
        nb_models = len(self.config.models)

        terminal_emulator = None
        if entrypoint == "cli":
            terminal_emulator = detect_terminal().value

        self.telemetry_client.send_new_session(
            has_agents_md=has_agents_md,
            nb_skills=nb_skills,
            nb_mcp_servers=nb_mcp_servers,
            nb_models=nb_models,
            entrypoint=entrypoint,
            client_name=client_name,
            client_version=client_version,
            terminal_emulator=terminal_emulator,
        )

    def _create_connector_registry(self) -> ConnectorRegistry | None:
        if not connectors_enabled():
            return None

        provider = self._base_config.get_mistral_provider()
        if provider is None:
            return None

        api_key_env = provider.api_key_env_var or "MISTRAL_API_KEY"
        api_key = os.getenv(api_key_env, "")
        if not api_key:
            return None

        server_url = get_server_url_from_api_base(provider.api_base)
        return ConnectorRegistry(api_key=api_key, server_url=server_url)

    def refresh_system_prompt(self) -> None:
        """Rebuild and replace the system prompt with current tool/skill state."""
        system_prompt = get_universal_system_prompt(
            self.tool_manager, self.config, self.skill_manager, self.agent_manager
        )
        self.messages.update_system_prompt(system_prompt)

    def _select_backend(self) -> BackendLike:
        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)
        timeout = self.config.api_timeout
        return BACKEND_FACTORY[provider.backend](provider=provider, timeout=timeout)

    async def _save_messages(self) -> None:
        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self._base_config,
            self.tool_manager,
            self.agent_profile,
        )

    async def inject_user_context(self, content: str) -> None:
        self.messages.append(LLMMessage(role=Role.user, content=content, injected=True))
        await self._save_messages()

    async def act(
        self, msg: str, client_message_id: str | None = None
    ) -> AsyncGenerator[BaseEvent, None]:
        self._clean_message_history()
        self.rewind_manager.create_checkpoint()
        try:
            model_name = self.config.get_active_model().name
        except ValueError:
            model_name = None
        async with agent_span(model=model_name, session_id=self.session_id):
            async for event in self._conversation_loop(
                msg, client_message_id=client_message_id
            ):
                yield event

    @property
    def teleport_service(self) -> TeleportService:
        if not _TELEPORT_AVAILABLE:
            raise TeleportError(
                "Teleport requires git to be installed. "
                "Please install git and try again."
            )

        if self._teleport_service is None:
            if _TeleportService is None:
                raise TeleportError("_TeleportService is unexpectedly None")
            self._teleport_service = _TeleportService(
                session_logger=self.session_logger,
                nuage_base_url=self.config.nuage_base_url,
                nuage_workflow_id=self.config.nuage_workflow_id,
                nuage_api_key=self.config.nuage_api_key,
                nuage_task_queue=self.config.nuage_task_queue,
                vibe_config=self._base_config,
            )
        return self._teleport_service

    def teleport_to_vibe_nuage(
        self, prompt: str | None
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        from vibe.core.teleport.nuage import TeleportSession

        session = TeleportSession(
            metadata={
                "agent": self.agent_profile.name,
                "model": self.config.active_model,
                "stats": self.stats.model_dump(),
            },
            messages=[msg.model_dump(exclude_none=True) for msg in self.messages[1:]],
        )
        return self._teleport_generator(prompt, session)

    async def _teleport_generator(
        self, prompt: str | None, session: TeleportSession
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        from vibe.core.teleport.errors import ServiceTeleportError

        try:
            async with self.teleport_service:
                gen = self.teleport_service.execute(prompt=prompt, session=session)
                response: TeleportPushResponseEvent | None = None
                while True:
                    try:
                        event = await gen.asend(response)
                        response = yield event
                    except StopAsyncIteration:
                        break
        except ServiceTeleportError as e:
            raise TeleportError(str(e)) from e
        finally:
            self._teleport_service = None

    def _setup_middleware(self) -> None:
        """Configure middleware pipeline for this conversation."""
        self.middleware_pipeline.clear()

        if self._max_turns is not None:
            self.middleware_pipeline.add(TurnLimitMiddleware(self._max_turns))

        if self._max_price is not None:
            self.middleware_pipeline.add(PriceLimitMiddleware(self._max_price))

        self.middleware_pipeline.add(AutoCompactMiddleware())
        if self.config.context_warnings:
            self.middleware_pipeline.add(ContextWarningMiddleware(0.5))

        self.middleware_pipeline.add(
            ReadOnlyAgentMiddleware(
                lambda: self.agent_profile,
                BuiltinAgentName.PLAN,
                lambda: make_plan_agent_reminder(self._plan_session.plan_file_path_str),
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
                old_tokens = result.metadata.get(
                    "old_tokens", self.stats.context_tokens
                )
                threshold = result.metadata.get(
                    "threshold", self.config.get_active_model().auto_compact_threshold
                )
                tool_call_id = str(uuid4())

                yield CompactStartEvent(
                    tool_call_id=tool_call_id,
                    current_context_tokens=old_tokens,
                    threshold=threshold,
                )
                self.telemetry_client.send_auto_compact_triggered()

                summary = await self.compact()

                yield CompactEndEvent(
                    tool_call_id=tool_call_id,
                    old_context_tokens=old_tokens,
                    new_context_tokens=self.stats.context_tokens,
                    summary_length=len(summary),
                )

            case MiddlewareAction.CONTINUE:
                pass

    def _get_context(self) -> ConversationContext:
        return ConversationContext(
            messages=self.messages, stats=self.stats, config=self.config
        )

    def _build_metadata(self) -> dict[str, str]:
        base = self.entrypoint_metadata.model_dump() if self.entrypoint_metadata else {}
        metadata = base | {
            "session_id": self.session_id,
            "is_user_prompt": "true" if self._is_user_prompt_call else "false",
            "call_type": (
                "main_call" if self._is_user_prompt_call else "secondary_call"
            ),
            "call_source": "vibe_code",
        }
        if self._current_user_message_id is not None:
            metadata["message_id"] = self._current_user_message_id
        return metadata

    def _get_extra_headers(self, provider: ProviderConfig) -> dict[str, str]:
        headers: dict[str, str] = {
            "user-agent": get_user_agent(provider.backend),
            "x-affinity": self.session_id,
        }
        return headers

    async def _conversation_loop(
        self, user_msg: str, client_message_id: str | None = None
    ) -> AsyncGenerator[BaseEvent]:
        user_message = LLMMessage(
            role=Role.user, content=user_msg, message_id=client_message_id
        )
        self.messages.append(user_message)
        self.stats.steps += 1
        self._current_user_message_id = user_message.message_id

        if user_message.message_id is None:
            raise AgentLoopError("User message must have a message_id")

        yield UserMessageEvent(content=user_msg, message_id=user_message.message_id)

        try:
            should_break_loop = False
            first_llm_turn = True
            while not should_break_loop:
                self._is_user_prompt_call = False
                result = await self.middleware_pipeline.run_before_turn(
                    self._get_context()
                )
                async for event in self._handle_middleware_result(result):
                    yield event

                if result.action == MiddlewareAction.STOP:
                    return

                self.stats.steps += 1
                user_cancelled = False
                if first_llm_turn:
                    self._is_user_prompt_call = True
                    first_llm_turn = False
                async for event in self._perform_llm_turn():
                    if is_user_cancellation_event(event):
                        user_cancelled = True
                    yield event
                    await self._save_messages()
                self._is_user_prompt_call = False

                last_message = self.messages[-1]
                should_break_loop = last_message.role != Role.tool

                if user_cancelled:
                    return

        finally:
            await self._save_messages()

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

    async def _process_one_tool_call(
        self, tool_call: ResolvedToolCall
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent]:
        async with tool_span(
            tool_name=tool_call.tool_name,
            call_id=tool_call.call_id,
            arguments=tool_call.validated_args.model_dump_json(),
        ) as span:
            async for event in self._execute_tool_call(span, tool_call):
                yield event

    async def _execute_tool_call(
        self, span: trace.Span, tool_call: ResolvedToolCall
    ) -> AsyncGenerator[ToolResultEvent | ToolStreamEvent]:
        try:
            tool_instance = self.tool_manager.get(tool_call.tool_name)
        except Exception as exc:
            error_msg = f"Error getting tool '{tool_call.tool_name}': {exc}"
            yield self._tool_failure_event(tool_call, error_msg, span=span)
            return

        decision: ToolDecision | None = None
        try:
            decision = await self._should_execute_tool(
                tool_instance, tool_call.validated_args, tool_call.call_id
            )

            if decision.verdict == ToolExecutionResponse.SKIP:
                self.stats.tool_calls_rejected += 1
                skip_reason = decision.feedback or str(
                    get_user_cancellation_message(
                        CancellationReason.TOOL_SKIPPED, tool_call.tool_name
                    )
                )
                yield ToolResultEvent(
                    tool_name=tool_call.tool_name,
                    tool_class=tool_call.tool_class,
                    skipped=True,
                    skip_reason=skip_reason,
                    cancelled=f"<{CANCELLATION_TAG}>" in skip_reason,
                    tool_call_id=tool_call.call_id,
                )
                self._handle_tool_response(
                    tool_call, skip_reason, "skipped", decision, span=span
                )
                return

            self.stats.tool_calls_agreed += 1

            snapshot = tool_instance.get_file_snapshot(tool_call.validated_args)
            if snapshot is not None:
                self.rewind_manager.add_snapshot(snapshot)

            start_time = time.perf_counter()
            result_model = None
            async for item in tool_instance.invoke(
                ctx=InvokeContext(
                    tool_call_id=tool_call.call_id,
                    agent_manager=self.agent_manager,
                    session_dir=self.session_logger.session_dir,
                    entrypoint_metadata=self.entrypoint_metadata,
                    approval_callback=self.approval_callback,
                    user_input_callback=self.user_input_callback,
                    sampling_callback=self._sampling_handler,
                    plan_file_path=self._plan_session.plan_file_path,
                    switch_agent_callback=self.switch_agent,
                    skill_manager=self.skill_manager,
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
            self._handle_tool_response(
                tool_call, text, "success", decision, result_dict, span=span
            )
            yield ToolResultEvent(
                tool_name=tool_call.tool_name,
                tool_class=tool_call.tool_class,
                result=result_model,
                cancelled=getattr(result_model, "cancelled", False),
                duration=duration,
                tool_call_id=tool_call.call_id,
            )
            self.stats.tool_calls_succeeded += 1

        except asyncio.CancelledError:
            cancel = str(
                get_user_cancellation_message(CancellationReason.TOOL_INTERRUPTED)
            )
            self.stats.tool_calls_failed += 1
            yield self._tool_failure_event(
                tool_call, cancel, decision, cancelled=True, span=span
            )
            raise

        except Exception as exc:
            error_msg = f"<{TOOL_ERROR_TAG}>{tool_instance.get_name()} failed: {exc}</{TOOL_ERROR_TAG}>"
            if isinstance(exc, ToolPermissionError):
                self.stats.tool_calls_agreed -= 1
                self.stats.tool_calls_rejected += 1
            else:
                self.stats.tool_calls_failed += 1
            yield self._tool_failure_event(tool_call, error_msg, decision, span=span)

    async def _handle_tool_calls(
        self, resolved: ResolvedMessage
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent | ToolStreamEvent]:
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

    async def _execute_tool_to_queue(
        self,
        tc: ResolvedToolCall,
        queue: asyncio.Queue[ToolCallEvent | ToolResultEvent | ToolStreamEvent | None],
    ) -> None:
        """Run a single tool call, sending events to the queue."""
        async for event in self._process_one_tool_call(tc):
            await queue.put(event)

    async def _run_tools_concurrently(
        self, tool_calls: list[ResolvedToolCall]
    ) -> AsyncGenerator[ToolCallEvent | ToolResultEvent | ToolStreamEvent]:
        """Execute multiple tool calls concurrently, yielding events as they arrive."""
        queue: asyncio.Queue[
            ToolCallEvent | ToolResultEvent | ToolStreamEvent | None
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
            status=status,
            decision=decision,
            result=result,
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

    async def _chat(
        self, max_tokens: int | None = None, model_override: ModelConfig | None = None
    ) -> LLMChunk:
        active_model = model_override or self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)

        available_tools = self.format_handler.get_available_tools(self.tool_manager)
        tool_choice = self.format_handler.get_tool_choice()

        try:
            start_time = time.perf_counter()
            result = await self.backend.complete(
                model=active_model,
                messages=self.messages,
                temperature=active_model.temperature,
                tools=available_tools,
                tool_choice=tool_choice,
                extra_headers=self._get_extra_headers(provider),
                max_tokens=max_tokens,
                metadata=self._build_metadata(),
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
            self.messages.append(processed_message)
            return LLMChunk(message=processed_message, usage=result.usage)

        except Exception as e:
            if _should_raise_rate_limit_error(e):
                raise RateLimitError(provider.name, active_model.name) from e

            raise RuntimeError(
                f"API error from {provider.name} (model: {active_model.name}): {e}"
            ) from e

    async def _chat_streaming(
        self, max_tokens: int | None = None
    ) -> AsyncGenerator[LLMChunk]:
        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)

        available_tools = self.format_handler.get_available_tools(self.tool_manager)
        tool_choice = self.format_handler.get_tool_choice()
        try:
            start_time = time.perf_counter()
            usage = LLMUsage()
            chunk_agg: LLMChunk | None = None
            async for chunk in self.backend.complete_streaming(
                model=active_model,
                messages=self.messages,
                temperature=active_model.temperature,
                tools=available_tools,
                tool_choice=tool_choice,
                extra_headers=self._get_extra_headers(provider),
                max_tokens=max_tokens,
                metadata=self._build_metadata(),
            ):
                if chunk.correlation_id:
                    self.telemetry_client.last_correlation_id = chunk.correlation_id
                processed_message = self.format_handler.process_api_response_message(
                    chunk.message
                )
                processed_chunk = LLMChunk(message=processed_message, usage=chunk.usage)
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

        except Exception as e:
            if _should_raise_rate_limit_error(e):
                raise RateLimitError(provider.name, active_model.name) from e

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

    async def _should_execute_tool(
        self, tool: BaseTool, args: BaseModel, tool_call_id: str
    ) -> ToolDecision:
        if self.auto_approve:
            return ToolDecision(
                verdict=ToolExecutionResponse.EXECUTE,
                approval_type=ToolPermission.ALWAYS,
            )

        async with self._approval_lock:
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
                        if not self._is_permission_covered(tool_name, rp)
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
                        if self.messages[j].tool_call_id:
                            responded_ids.add(self.messages[j].tool_call_id)
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

    def _reset_session(self) -> None:
        self.session_id = str(uuid4())
        self.session_logger.reset_session(self.session_id)

    async def clear_history(self) -> None:
        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self._base_config,
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
        self._reset_session()

    async def compact(self) -> str:
        try:
            self._clean_message_history()
            await self.session_logger.save_interaction(
                self.messages,
                self.stats,
                self._base_config,
                self.tool_manager,
                self.agent_profile,
            )

            summary_request = UtilityPrompt.COMPACT.read()
            self.stats.steps += 1

            with self.messages.silent():
                self.messages.append(
                    LLMMessage(role=Role.user, content=summary_request)
                )
                summary_result = await self._chat(
                    model_override=self.config.get_compaction_model()
                )

            if summary_result.usage is None:
                raise AgentLoopLLMResponseError(
                    "Usage data missing in compaction summary response"
                )
            summary_content = summary_result.message.content or ""

            system_message = self.messages[0]
            summary_message = LLMMessage(role=Role.user, content=summary_content)
            self.messages.reset([system_message, summary_message])

            active_model = self.config.get_active_model()
            provider = self.config.get_provider_for_model(active_model)

            actual_context_tokens = await self.backend.count_tokens(
                model=active_model,
                messages=self.messages,
                tools=self.format_handler.get_available_tools(self.tool_manager),
                extra_headers={"user-agent": get_user_agent(provider.backend)},
                metadata=self._build_metadata(),
            )

            self.stats.context_tokens = actual_context_tokens

            self._reset_session()
            await self.session_logger.save_interaction(
                self.messages,
                self.stats,
                self._base_config,
                self.tool_manager,
                self.agent_profile,
            )

            self.middleware_pipeline.reset(reset_reason=ResetReason.COMPACT)

            return summary_content or ""

        except Exception:
            await self.session_logger.save_interaction(
                self.messages,
                self.stats,
                self._base_config,
                self.tool_manager,
                self.agent_profile,
            )
            raise

    async def switch_agent(self, agent_name: str) -> None:
        if agent_name == self.agent_profile.name:
            return
        self.agent_manager.switch_profile(agent_name)
        await self.reload_with_initial_messages(reset_middleware=False)

    async def reload_with_initial_messages(
        self,
        base_config: VibeConfig | None = None,
        max_turns: int | None = None,
        max_price: float | None = None,
        reset_middleware: bool = True,
    ) -> None:
        # Force an immediate yield to allow the UI to update before heavy sync work.
        # When there are no messages, save_interaction returns early without any await,
        # so the coroutine would run synchronously through ToolManager, SkillManager,
        # and system prompt generation without yielding control to the event loop.
        await asyncio.sleep(0)

        await self.session_logger.save_interaction(
            self.messages,
            self.stats,
            self._base_config,
            self.tool_manager,
            self.agent_profile,
        )

        if base_config is not None:
            self._base_config = base_config
            self.agent_manager.invalidate_config()

        self.backend = self.backend_factory()

        if max_turns is not None:
            self._max_turns = max_turns
        if max_price is not None:
            self._max_price = max_price

        self.tool_manager = ToolManager(
            lambda: self.config,
            mcp_registry=self.mcp_registry,
            connector_registry=self.connector_registry,
        )
        self.skill_manager = SkillManager(lambda: self.config)

        new_system_prompt = get_universal_system_prompt(
            self.tool_manager, self.config, self.skill_manager, self.agent_manager
        )

        self.messages.update_system_prompt(new_system_prompt)

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
