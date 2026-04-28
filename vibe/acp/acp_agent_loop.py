from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
import inspect
import logging
import os
from pathlib import Path
import sys
from typing import Any, cast, override
from uuid import uuid4

from acp import (
    PROTOCOL_VERSION,
    Agent as AcpAgent,
    Client,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    run_agent,
)
from acp.helpers import ContentBlock, SessionUpdate, update_available_commands
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    AuthenticateResponse,
    AuthMethodAgent,
    AvailableCommand,
    AvailableCommandInput,
    ClientCapabilities,
    CloseSessionResponse,
    ConfigOptionUpdate,
    ContentToolCallContent,
    Cost,
    EnvVarAuthMethod,
    ForkSessionResponse,
    HttpMcpServer,
    Implementation,
    ListSessionsResponse,
    McpServerStdio,
    PromptCapabilities,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionForkCapabilities,
    SessionInfo,
    SessionListCapabilities,
    SetSessionConfigOptionResponse,
    SseMcpServer,
    TerminalAuthMethod,
    TextContentBlock,
    TextResourceContents,
    ToolCallProgress,
    ToolCallUpdate,
    UnstructuredCommandInput,
    Usage,
    UsageUpdate,
)
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from vibe import VIBE_ROOT, __version__
from vibe.acp.acp_logger import acp_message_observer
from vibe.acp.commands import AcpCommandRegistry
from vibe.acp.exceptions import (
    ConfigurationError,
    ContextTooLongError,
    ConversationLimitError,
    InternalError,
    InvalidRequestError,
    NotImplementedMethodError,
    RateLimitError,
    SessionLoadError,
    SessionNotFoundError,
    UnauthenticatedError,
)
from vibe.acp.session import AcpSessionLoop
from vibe.acp.tools.base import BaseAcpTool
from vibe.acp.tools.session_update import (
    tool_call_session_update,
    tool_result_session_update,
)
from vibe.acp.utils import (
    THINKING_LEVELS,
    ThinkingLevel,
    ToolOption,
    build_mode_state,
    build_model_state,
    build_permission_options,
    create_assistant_message_replay,
    create_compact_end_session_update,
    create_compact_start_session_update,
    create_reasoning_replay,
    create_tool_call_replay,
    create_tool_result_replay,
    create_user_message_replay,
    get_proxy_help_text,
    is_valid_acp_mode,
    make_thinking_response,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import CHAT as CHAT_AGENT, BuiltinAgentName
from vibe.core.autocompletion.path_prompt_adapter import render_path_prompt
from vibe.core.config import (
    MissingAPIKeyError,
    SessionLoggingConfig,
    VibeConfig,
    load_dotenv_values,
)
from vibe.core.data_retention import DATA_RETENTION_MESSAGE
from vibe.core.hooks.config import load_hooks_from_fs
from vibe.core.proxy_setup import (
    ProxySetupError,
    parse_proxy_command,
    set_proxy_var,
    unset_proxy_var,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.skills.manager import SkillManager
from vibe.core.telemetry.build_metadata import build_entrypoint_metadata
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import EntrypointMetadata
from vibe.core.tools.permissions import RequiredPermission
from vibe.core.types import (
    AgentProfileChangedEvent,
    ApprovalCallback,
    ApprovalResponse,
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    ContextTooLongError as CoreContextTooLongError,
    LLMMessage,
    RateLimitError as CoreRateLimitError,
    ReasoningEvent,
    Role,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
)
from vibe.core.utils import (
    CancellationReason,
    ConversationLimitException,
    get_user_cancellation_message,
)

logger = logging.getLogger("vibe")


class ForkSessionParams(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: str | None = Field(default=None, alias="messageId")


class TelemetrySendNotification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str
    properties: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(validation_alias=AliasChoices("session_id", "sessionId"))


_EVENT_DISPATCHERS: dict[str, Callable[[TelemetryClient, dict[str, Any]], None]] = {}


def _resolved_user_message_id(client_message_id: str | None) -> str:
    if client_message_id is not None:
        return client_message_id
    return str(uuid4())


class VibeAcpAgentLoop(AcpAgent):
    client: Client

    def __init__(self) -> None:
        self.sessions: dict[str, AcpSessionLoop] = {}
        self.client_capabilities: ClientCapabilities | None = None
        self.client_info: Implementation | None = None

    @override
    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        self.client_capabilities = client_capabilities
        self.client_info = client_info

        # The ACP Agent process can be launched in 3 different ways, depending on installation
        #  - dev mode: `uv run vibe-acp`, ran from the project root
        #  - uv tool install: `vibe-acp`, similar to dev mode, but uv takes care of path resolution
        #  - bundled binary: `./vibe-acp` from binary location
        # The 2 first modes are working similarly, under the hood uv runs `/some/python /my/entrypoint.py``
        # The last mode is quite different as our bundler also includes the python install.
        # So sys.executable is already /path/to/binary/vibe-acp.
        # For this reason, we make a distinction in the way we call the setup command
        command = sys.executable
        if "python" not in Path(command).name:
            # It's the case for bundled binaries, we don't need any other arguments
            args = ["--setup"]
        else:
            script_name = sys.argv[0]
            args = [script_name, "--setup"]

        supports_terminal_auth = (
            self.client_capabilities
            and self.client_capabilities.field_meta
            and self.client_capabilities.field_meta.get("terminal-auth") is True
        )

        auth_methods: list[EnvVarAuthMethod | TerminalAuthMethod | AuthMethodAgent] = (
            [
                TerminalAuthMethod(
                    type="terminal",
                    id="vibe-setup",
                    name="Register your API Key",
                    description="Register your API Key inside Mistral Vibe",
                    field_meta={
                        "terminal-auth": {
                            "command": command,
                            "args": args,
                            "label": "Mistral Vibe Setup",
                        }
                    },
                )
            ]
            if supports_terminal_auth
            else []
        )

        response = InitializeResponse(
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    audio=False, embedded_context=True, image=False
                ),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities(),
                    list=SessionListCapabilities(),
                    fork=SessionForkCapabilities(),
                ),
            ),
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(
                name="@mistralai/mistral-vibe",
                title="Mistral Vibe",
                version=__version__,
            ),
            auth_methods=auth_methods,
        )
        return response

    @override
    async def authenticate(
        self, method_id: str, **kwargs: Any
    ) -> AuthenticateResponse | None:
        raise NotImplementedMethodError("authenticate")

    def _build_entrypoint_metadata(self) -> EntrypointMetadata:
        return build_entrypoint_metadata(
            agent_entrypoint="acp",
            agent_version=__version__,
            client_name=self.client_info.name if self.client_info else "",
            client_version=self.client_info.version if self.client_info else "",
        )

    def _load_config(self) -> VibeConfig:
        try:
            config = VibeConfig.load(disabled_tools=["ask_user_question"])
            config.tool_paths.extend(self._get_acp_tool_overrides())
            return config
        except MissingAPIKeyError as e:
            raise UnauthenticatedError.from_missing_api_key(e) from e
        except Exception as e:
            raise ConfigurationError(str(e)) from e

    async def _create_acp_session(
        self, session_id: str, agent_loop: AgentLoop
    ) -> AcpSessionLoop:
        command_registry = AcpCommandRegistry()
        session = AcpSessionLoop(
            id=session_id, agent_loop=agent_loop, command_registry=command_registry
        )
        self.sessions[session.id] = session

        async def _on_commands_changed() -> None:
            session.spawn(self._send_available_commands(session))

        command_registry.set_on_changed(_on_commands_changed)

        if not agent_loop.bypass_tool_permissions:
            agent_loop.set_approval_callback(self._create_approval_callback(session.id))

        session.spawn(self._send_available_commands(session))

        return session

    def _create_agent_loop(
        self, config: VibeConfig, agent_name: str, hook_config_result: Any = None
    ) -> AgentLoop:
        agent_loop = AgentLoop(
            config=config,
            agent_name=agent_name,
            enable_streaming=True,
            entrypoint_metadata=self._build_entrypoint_metadata(),
            defer_heavy_init=True,
            hook_config_result=hook_config_result,
        )
        agent_loop.agent_manager.register_agent(CHAT_AGENT)
        return agent_loop

    def _build_session_state(
        self, session: AcpSessionLoop
    ) -> tuple[Any, Any, Any, Any]:
        modes_state, modes_config = build_mode_state(
            list(session.agent_loop.agent_manager.available_agents.values()),
            session.agent_loop.agent_profile.name,
        )
        models_state, models_config = build_model_state(
            session.agent_loop.config.models, session.agent_loop.config.active_model
        )
        return modes_state, modes_config, models_state, models_config

    @override
    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        load_dotenv_values()
        os.chdir(cwd)

        config = self._load_config()
        hook_config_result = load_hooks_from_fs(config)

        try:
            agent_loop = self._create_agent_loop(
                config, BuiltinAgentName.DEFAULT, hook_config_result=hook_config_result
            )
            # NOTE: For now, we pin session.id to agent_loop.session_id right after init time.
            # We should just use agent_loop.session_id everywhere, but it can still change during
            # session lifetime (e.g. agent_loop.compact is called).
            # We should refactor agent_loop.session_id to make it immutable in ACP context.
            session = await self._create_acp_session(agent_loop.session_id, agent_loop)
        except Exception as e:
            raise ConfigurationError(str(e)) from e

        agent_loop.emit_new_session_telemetry()

        modes_state, _, models_state, _ = self._build_session_state(session)

        return NewSessionResponse(
            session_id=session.id,
            models=models_state,
            modes=modes_state,
            config_options=self._build_config_options(session),
        )

    def _get_acp_tool_overrides(self) -> list[Path]:
        overrides = ["todo"]

        if self.client_capabilities:
            if self.client_capabilities.terminal:
                overrides.append("bash")
            if self.client_capabilities.fs:
                fs = self.client_capabilities.fs
                if fs.read_text_file:
                    overrides.append("read_file")
                if fs.write_text_file:
                    overrides.extend(["write_file", "search_replace"])

        return [
            VIBE_ROOT / "acp" / "tools" / "builtins" / f"{override}.py"
            for override in overrides
        ]

    def _create_approval_callback(self, session_id: str) -> ApprovalCallback:
        session = self._get_session(session_id)

        def _handle_permission_selection(
            option_id: str,
            tool_name: str,
            required_permissions: list[RequiredPermission] | None,
        ) -> tuple[ApprovalResponse, str | None]:
            match option_id:
                case ToolOption.ALLOW_ONCE:
                    return (ApprovalResponse.YES, None)
                case ToolOption.ALLOW_ALWAYS:
                    session.agent_loop.approve_always(tool_name, required_permissions)
                    return (ApprovalResponse.YES, None)
                case ToolOption.REJECT_ONCE:
                    return (
                        ApprovalResponse.NO,
                        "User rejected the tool call, provide an alternative plan",
                    )
                case _:
                    return (ApprovalResponse.NO, f"Unknown option: {option_id}")

        async def approval_callback(
            tool_name: str,
            args: BaseModel,
            tool_call_id: str,
            required_permissions: list | None = None,
        ) -> tuple[ApprovalResponse, str | None]:
            typed_permissions: list[RequiredPermission] | None = (
                [
                    rp
                    for rp in required_permissions
                    if isinstance(rp, RequiredPermission)
                ]
                if required_permissions
                else None
            )

            tool_call = ToolCallUpdate(tool_call_id=tool_call_id)
            options = build_permission_options(typed_permissions)

            response = await self.client.request_permission(
                session_id=session_id, tool_call=tool_call, options=options
            )

            if response.outcome.outcome == "selected":
                outcome = cast(AllowedOutcome, response.outcome)
                return _handle_permission_selection(
                    outcome.option_id, tool_name, typed_permissions
                )
            else:
                return (
                    ApprovalResponse.NO,
                    str(
                        get_user_cancellation_message(
                            CancellationReason.OPERATION_CANCELLED
                        )
                    ),
                )

        return approval_callback

    def _get_session(self, session_id: str) -> AcpSessionLoop:
        if session_id not in self.sessions:
            raise SessionNotFoundError(session_id)
        return self.sessions[session_id]

    def _build_usage(self, session: AcpSessionLoop) -> Usage:
        stats = session.agent_loop.stats
        return Usage(
            input_tokens=stats.session_prompt_tokens,
            output_tokens=stats.session_completion_tokens,
            total_tokens=stats.session_total_llm_tokens,
        )

    def _build_usage_update(self, session: AcpSessionLoop) -> UsageUpdate:
        stats = session.agent_loop.stats
        active_model = session.agent_loop.config.get_active_model()
        cost = (
            Cost(amount=stats.session_cost, currency="USD")
            if stats.input_price_per_million > 0 or stats.output_price_per_million > 0
            else None
        )
        return UsageUpdate(
            session_update="usage_update",
            used=stats.context_tokens,
            size=active_model.auto_compact_threshold,
            cost=cost,
        )

    def _send_usage_update(self, session: AcpSessionLoop) -> None:
        async def _send() -> None:
            try:
                update = self._build_usage_update(session)
                await self.client.session_update(session_id=session.id, update=update)
            except Exception:
                pass

        session.spawn(_send())

    async def _replay_tool_calls(self, session_id: str, msg: LLMMessage) -> None:
        if not msg.tool_calls:
            return
        for tool_call in msg.tool_calls:
            if tool_call.id and tool_call.function.name:
                update = create_tool_call_replay(
                    tool_call.id, tool_call.function.name, tool_call.function.arguments
                )
                await self.client.session_update(session_id=session_id, update=update)

    async def _replay_conversation_history(
        self, session_id: str, messages: list[LLMMessage]
    ) -> None:
        for msg in messages:
            if msg.role == Role.user:
                update = create_user_message_replay(msg)
                await self.client.session_update(session_id=session_id, update=update)

            elif msg.role == Role.assistant:
                if reasoning_update := create_reasoning_replay(msg):
                    await self.client.session_update(
                        session_id=session_id, update=reasoning_update
                    )
                if text_update := create_assistant_message_replay(msg):
                    await self.client.session_update(
                        session_id=session_id, update=text_update
                    )
                await self._replay_tool_calls(session_id, msg)

            elif msg.role == Role.tool:
                if result_update := create_tool_result_replay(msg):
                    await self.client.session_update(
                        session_id=session_id, update=result_update
                    )

    async def _send_available_commands(self, session: AcpSessionLoop) -> None:
        commands: list[AvailableCommand] = []

        for cmd in session.command_registry.commands.values():
            input_spec = (
                AvailableCommandInput(
                    root=UnstructuredCommandInput(hint=cmd.input_hint)
                )
                if cmd.input_hint
                else None
            )
            commands.append(
                AvailableCommand(
                    name=cmd.name, description=cmd.description, input=input_spec
                )
            )

        builtin_names = set(session.command_registry.commands)
        for skill in session.agent_loop.skill_manager.available_skills.values():
            if not skill.user_invocable or skill.name in builtin_names:
                continue
            commands.append(
                AvailableCommand(
                    name=skill.name,
                    description=skill.description,
                    input=AvailableCommandInput(
                        root=UnstructuredCommandInput(hint="instructions for the skill")
                    ),
                )
            )

        await self.client.session_update(
            session_id=session.id, update=update_available_commands(commands)
        )

    @override
    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        load_dotenv_values()
        os.chdir(cwd)

        config = self._load_config()
        hook_config_result = load_hooks_from_fs(config)

        session_dir = SessionLoader.find_session_by_id(
            session_id, config.session_logging
        )
        if session_dir is None:
            raise SessionNotFoundError(session_id)

        try:
            loaded_messages, metadata = SessionLoader.load_session(session_dir)
        except Exception as e:
            raise SessionLoadError(session_id, str(e)) from e

        agent_loop = self._create_agent_loop(
            config, BuiltinAgentName.DEFAULT, hook_config_result=hook_config_result
        )
        loaded_session_id = metadata.get("session_id", agent_loop.session_id)
        agent_loop.session_id = loaded_session_id
        agent_loop.parent_session_id = metadata.get("parent_session_id")
        agent_loop.session_logger.resume_existing_session(
            loaded_session_id, session_dir
        )

        non_system_messages = [
            msg for msg in loaded_messages if msg.role != Role.system
        ]
        if non_system_messages:
            agent_loop.messages.extend(non_system_messages)
        session = await self._create_acp_session(session_id, agent_loop)
        await self._replay_conversation_history(session.id, non_system_messages)
        self._send_usage_update(session)

        modes_state, _, models_state, _ = self._build_session_state(session)

        return LoadSessionResponse(
            models=models_state,
            modes=modes_state,
            config_options=self._build_config_options(session),
        )

    async def _apply_mode_change(self, session: AcpSessionLoop, mode_id: str) -> bool:
        profiles = list(session.agent_loop.agent_manager.available_agents.values())
        if not is_valid_acp_mode(profiles, mode_id):
            return False

        await session.agent_loop.switch_agent(mode_id)

        if session.agent_loop.bypass_tool_permissions:
            session.agent_loop.approval_callback = None
        else:
            session.agent_loop.set_approval_callback(
                self._create_approval_callback(session.id)
            )

        return True

    async def _reload_config(self, session: AcpSessionLoop) -> None:
        new_config = VibeConfig.load(
            tool_paths=session.agent_loop.config.tool_paths,
            disabled_tools=["ask_user_question"],
        )
        await session.agent_loop.reload_with_initial_messages(base_config=new_config)

    async def _apply_model_change(self, session: AcpSessionLoop, model_id: str) -> bool:
        model_aliases = [model.alias for model in session.agent_loop.config.models]
        if model_id not in model_aliases:
            return False

        VibeConfig.save_updates({"active_model": model_id})
        await self._reload_config(session)
        return True

    async def _apply_thinking_change(
        self, session: AcpSessionLoop, level: ThinkingLevel
    ) -> bool:
        session.agent_loop.config.set_thinking(level)
        await self._reload_config(session)
        return True

    @override
    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        session = self._get_session(session_id)

        if not await self._apply_mode_change(session, mode_id):
            return None

        return SetSessionModeResponse()

    @override
    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        session = self._get_session(session_id)

        if not await self._apply_model_change(session, model_id):
            return None

        return SetSessionModelResponse()

    @override
    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        session = self._get_session(session_id)

        match config_id:
            case "mode" if isinstance(value, str):
                success = await self._apply_mode_change(session, value)
            case "model" if isinstance(value, str):
                success = await self._apply_model_change(session, value)
            case "thinking" if isinstance(value, str) and value in THINKING_LEVELS:
                success = await self._apply_thinking_change(
                    session, cast(ThinkingLevel, value)
                )
            case _:
                success = False

        if not success:
            return None

        return SetSessionConfigOptionResponse(
            config_options=self._build_config_options(session)
        )

    @override
    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        try:
            config = VibeConfig.load()
            session_logging_config = config.session_logging
        except MissingAPIKeyError:
            session_logging_config = SessionLoggingConfig()

        session_data = SessionLoader.list_sessions(session_logging_config, cwd=cwd)

        sessions = [
            SessionInfo(
                session_id=s["session_id"],
                cwd=s["cwd"],
                title=s.get("title"),
                updated_at=s.get("end_time"),
            )
            for s in sorted(
                session_data, key=lambda s: s.get("end_time") or "", reverse=True
            )
        ]

        return ListSessionsResponse(sessions=sessions)

    @override
    async def prompt(
        self,
        prompt: list[ContentBlock],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        session = self._get_session(session_id)

        if session.prompt_task is not None:
            raise InvalidRequestError(
                "Concurrent prompts are not supported yet, wait for agent loop to finish"
            )

        text_prompt = self._build_text_prompt(prompt)
        resolved_message_id = _resolved_user_message_id(message_id)

        if command_response := await self._maybe_handle_builtin_command(
            session, text_prompt, resolved_message_id
        ):
            return command_response

        try:
            skill = session.agent_loop.skill_manager.parse_skill_command(text_prompt)
        except OSError as e:
            raise InternalError(f"Failed to read skill file: {e}") from e

        if skill:
            session.agent_loop.telemetry_client.send_slash_command_used(
                skill.name, "skill"
            )
            text_prompt = SkillManager.build_skill_prompt(text_prompt, skill)

        async def agent_loop_task() -> None:
            async for update in self._run_agent_loop(
                session, text_prompt, resolved_message_id
            ):
                await self.client.session_update(session_id=session.id, update=update)

        try:
            task = session.set_prompt_task(agent_loop_task())
            await task

        except asyncio.CancelledError:
            self._send_usage_update(session)
            return PromptResponse(
                stop_reason="cancelled",
                usage=self._build_usage(session),
                user_message_id=resolved_message_id,
            )

        except CoreRateLimitError as e:
            raise RateLimitError.from_core(e) from e

        except CoreContextTooLongError as e:
            raise ContextTooLongError.from_core(e) from e

        except ConversationLimitException as e:
            raise ConversationLimitError(str(e)) from e

        except Exception as e:
            raise InternalError(str(e)) from e

        self._send_usage_update(session)
        return PromptResponse(
            stop_reason="end_turn",
            usage=self._build_usage(session),
            user_message_id=resolved_message_id,
        )

    def _build_text_prompt(self, acp_prompt: list[ContentBlock]) -> str:
        text_prompt = ""
        for block in acp_prompt:
            separator = "\n\n" if text_prompt else ""
            match block.type:
                # NOTE: ACP supports annotations, but we don't use them here yet.
                case "text":
                    text_prompt = f"{text_prompt}{separator}{block.text}"
                case "resource":
                    block_content = (
                        block.resource.text
                        if isinstance(block.resource, TextResourceContents)
                        else block.resource.blob
                    )
                    fields = {"path": block.resource.uri, "content": block_content}
                    parts = [
                        f"{k}: {v}"
                        for k, v in fields.items()
                        if v is not None and (v or isinstance(v, (int, float)))
                    ]
                    block_prompt = "\n".join(parts)
                    text_prompt = f"{text_prompt}{separator}{block_prompt}"
                case "resource_link":
                    # NOTE: we currently keep more information than just the URI
                    # making it more detailed than the output of the read_file tool.
                    # This is OK, but might be worth testing how it affect performance.
                    fields = {
                        "uri": block.uri,
                        "name": block.name,
                        "title": block.title,
                        "description": block.description,
                        "mime_type": block.mime_type,
                        "size": block.size,
                    }
                    parts = [
                        f"{k}: {v}"
                        for k, v in fields.items()
                        if v is not None and (v or isinstance(v, (int, float)))
                    ]
                    block_prompt = "\n".join(parts)
                    text_prompt = f"{text_prompt}{separator}{block_prompt}"
                case _:
                    raise InvalidRequestError(
                        f"We currently don't support {block.type} content blocks"
                    )
        return text_prompt

    async def _maybe_handle_builtin_command(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse | None:
        normalized = text_prompt.strip().lower()
        parts = normalized.split(None, 1)
        if not parts or not parts[0].startswith("/"):
            return None

        cmd_name = parts[0][1:]  # strip leading "/"
        command = session.command_registry.get(cmd_name)
        if command is None:
            return None

        session.agent_loop.telemetry_client.send_slash_command_used(cmd_name, "builtin")
        handler = getattr(self, command.handler)
        return await handler(session, text_prompt, message_id)

    async def _run_agent_loop(
        self, session: AcpSessionLoop, prompt: str, client_message_id: str | None = None
    ) -> AsyncGenerator[SessionUpdate | UsageUpdate]:
        rendered_prompt = render_path_prompt(prompt, base_dir=Path.cwd())

        async with aclosing(
            session.agent_loop.act(rendered_prompt, client_message_id=client_message_id)
        ) as events:
            async for event in events:
                if isinstance(event, AssistantEvent):
                    yield AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text=event.content),
                        message_id=event.message_id,
                    )

                elif isinstance(event, ReasoningEvent):
                    yield AgentThoughtChunk(
                        session_update="agent_thought_chunk",
                        content=TextContentBlock(type="text", text=event.content),
                        message_id=event.message_id,
                    )

                elif isinstance(event, ToolCallEvent):
                    if issubclass(event.tool_class, BaseAcpTool):
                        event.tool_class.update_tool_state(
                            tool_manager=session.agent_loop.tool_manager,
                            client=self.client,
                            session_id=session.id,
                            tool_call_id=event.tool_call_id,
                        )

                    session_update = tool_call_session_update(event)
                    if session_update:
                        yield session_update

                elif isinstance(event, ToolResultEvent):
                    session_update = tool_result_session_update(event)
                    if session_update:
                        yield session_update
                    self._send_usage_update(session)

                elif isinstance(event, ToolStreamEvent):
                    yield ToolCallProgress(
                        session_update="tool_call_update",
                        tool_call_id=event.tool_call_id,
                        content=[
                            ContentToolCallContent(
                                type="content",
                                content=TextContentBlock(
                                    type="text", text=event.message
                                ),
                            )
                        ],
                    )

                elif isinstance(event, CompactStartEvent):
                    yield create_compact_start_session_update(event)

                elif isinstance(event, CompactEndEvent):
                    yield create_compact_end_session_update(event)

                elif isinstance(event, AgentProfileChangedEvent):
                    pass

    @override
    async def close_session(
        self, session_id: str, **kwargs: Any
    ) -> CloseSessionResponse | None:
        session = self._get_session(session_id)
        self.sessions.pop(session_id, None)

        await session.close()
        await self._close_agent_loop(session.agent_loop)

        return CloseSessionResponse()

    async def _close_agent_loop(self, agent_loop: AgentLoop) -> None:
        deferred_init_thread = agent_loop._deferred_init_thread
        if deferred_init_thread is not None and deferred_init_thread.is_alive():
            await asyncio.to_thread(deferred_init_thread.join)

        backend_close = getattr(agent_loop.backend, "close", None)
        if callable(backend_close):
            close_result = backend_close()
            if inspect.isawaitable(close_result):
                await close_result

        await agent_loop.telemetry_client.aclose()

    @override
    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self._get_session(session_id)
        await session.cancel_prompt()

    @override
    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        load_dotenv_values()
        os.chdir(cwd)

        source_session = self._get_session(session_id)
        try:
            message_id = ForkSessionParams.model_validate(kwargs).message_id
        except ValidationError as e:
            raise InvalidRequestError(f"Invalid fork parameters: {e}") from e
        if (
            source_session.prompt_task is not None
            and not source_session.prompt_task.done()
        ):
            raise InvalidRequestError(
                "Cannot fork a session while the agent loop is running"
            )

        try:
            agent_loop = await source_session.agent_loop.fork(message_id)
            agent_loop.agent_manager.register_agent(CHAT_AGENT)
            session = await self._create_acp_session(agent_loop.session_id, agent_loop)
        except InvalidRequestError:
            raise
        except ValueError as e:
            raise InvalidRequestError(str(e)) from e
        except Exception as e:
            raise ConfigurationError(str(e)) from e

        modes_state, _, models_state, _ = self._build_session_state(session)

        return ForkSessionResponse(
            session_id=session.id,
            models=models_state,
            modes=modes_state,
            config_options=self._build_config_options(session),
        )

    @override
    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        raise NotImplementedMethodError("resume_session")

    @override
    async def ext_method(self, method: str, params: dict) -> dict:
        raise NotImplementedMethodError("ext_method")

    @override
    async def ext_notification(self, method: str, params: dict) -> None:
        # ACP strips the leading "_" before delegating extension notifications here.
        if method == "telemetry/send":
            self._handle_telemetry_notification(params)

    def _handle_telemetry_notification(self, params: dict[str, Any]) -> None:
        try:
            notification = TelemetrySendNotification.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP telemetry notification: {exc}"
            ) from exc

        session = self.sessions.get(notification.session_id)
        if session is None:
            logger.warning(
                "Ignoring ACP telemetry notification because session could not be resolved: %s",
                notification.session_id,
            )
            return

        dispatcher = _EVENT_DISPATCHERS.get(notification.event)
        if dispatcher is None:
            logger.warning(
                "Ignoring unsupported ACP telemetry event: %s", notification.event
            )
            return

        dispatcher(session.agent_loop.telemetry_client, notification.properties)

    @override
    def on_connect(self, conn: Client) -> None:
        self.client = conn

    # -- Command handlers ------------------------------------------------------

    async def _command_reply(
        self, session: AcpSessionLoop, text: str, message_id: str
    ) -> PromptResponse:
        """Send a text message to the client and return an end-turn response."""
        await self.client.session_update(
            session_id=session.id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
                message_id=str(uuid4()),
            ),
        )
        return PromptResponse(stop_reason="end_turn", user_message_id=message_id)

    async def _handle_help(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        lines = ["### Available Commands", ""]
        for cmd in session.command_registry.commands.values():
            hint = f" `<{cmd.input_hint}>`" if cmd.input_hint else ""
            lines.append(f"- `/{cmd.name}`{hint}: {cmd.description}")

        builtin_names = set(session.command_registry.commands)
        invocable = {
            n: s
            for n, s in session.agent_loop.skill_manager.available_skills.items()
            if s.user_invocable and n not in builtin_names
        }
        if invocable:
            lines.extend(["", "### Available Skills", ""])
            for name, info in invocable.items():
                lines.append(f"- `/{name}`: {info.description}")

        return await self._command_reply(session, "\n".join(lines), message_id)

    async def _handle_compact(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        if len(session.agent_loop.messages) <= 1:
            return await self._command_reply(
                session, "No conversation history to compact yet.", message_id
            )

        tool_call_id = str(uuid4())
        old_tokens = session.agent_loop.stats.context_tokens
        parts = text_prompt.strip().split(None, 1)
        cmd_args = parts[1] if len(parts) > 1 else ""

        start_event = CompactStartEvent(
            current_context_tokens=old_tokens or 0,
            threshold=0,
            tool_call_id=tool_call_id,
        )
        await self.client.session_update(
            session_id=session.id,
            update=create_compact_start_session_update(start_event),
        )

        await session.agent_loop.compact(extra_instructions=cmd_args.strip())
        new_tokens = session.agent_loop.stats.context_tokens

        end_event = CompactEndEvent(
            old_context_tokens=old_tokens or 0,
            new_context_tokens=new_tokens or 0,
            summary_length=0,
            tool_call_id=tool_call_id,
        )
        await self.client.session_update(
            session_id=session.id, update=create_compact_end_session_update(end_event)
        )

        return PromptResponse(stop_reason="end_turn", user_message_id=message_id)

    async def _reload_session_config(self, session: AcpSessionLoop) -> None:
        """Reload config from disk and reinitialize the agent loop."""
        new_config = VibeConfig.load(
            tool_paths=session.agent_loop.config.tool_paths,
            disabled_tools=["ask_user_question"],
        )
        await session.agent_loop.reload_with_initial_messages(base_config=new_config)

    async def _handle_reload(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        try:
            await self._reload_session_config(session)
        except Exception as e:
            return await self._command_reply(
                session, f"Failed to reload config: {e}", message_id
            )

        try:
            await session.command_registry.notify_changed()
        except Exception as e:
            return await self._command_reply(
                session,
                f"Configuration reloaded, but failed to advertise updated commands: {e}",
                message_id,
            )

        return await self._command_reply(
            session,
            "Configuration reloaded (includes agent instructions and skills).",
            message_id,
        )

    async def _handle_log(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        logger = session.agent_loop.session_logger
        if not logger.enabled:
            return await self._command_reply(
                session, "Session logging is disabled in configuration.", message_id
            )

        return await self._command_reply(
            session,
            f"## Current Log Directory\n\n`{logger.session_dir}`\n\n"
            "You can send this directory to share your interaction.",
            message_id,
        )

    async def _handle_proxy_setup(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        parts = text_prompt.strip().split(None, 1)
        args = parts[1] if len(parts) > 1 else ""

        try:
            if not args:
                message = get_proxy_help_text()
            else:
                key, value = parse_proxy_command(args)
                if value is not None:
                    set_proxy_var(key, value)
                    message = (
                        f"Set `{key}={value}` in ~/.vibe/.env\n\n"
                        "Please start a new chat for changes to take effect."
                    )
                else:
                    unset_proxy_var(key)
                    message = (
                        f"Removed `{key}` from ~/.vibe/.env\n\n"
                        "Please start a new chat for changes to take effect."
                    )
        except ProxySetupError as e:
            message = f"Error: {e}"

        return await self._command_reply(session, message, message_id)

    def _build_config_options(
        self, session: AcpSessionLoop
    ) -> list[SessionConfigOptionSelect | SessionConfigOptionBoolean]:
        """Build the current modes + models config options for a session."""
        profiles = list(session.agent_loop.agent_manager.available_agents.values())
        _, modes_config = build_mode_state(
            profiles, session.agent_loop.agent_profile.name
        )
        _, models_config = build_model_state(
            session.agent_loop.config.models, session.agent_loop.config.active_model
        )
        thinking_config = make_thinking_response(
            session.agent_loop.config.get_active_model().thinking
        )
        return [modes_config, models_config, thinking_config]

    async def _send_config_option_update(self, session: AcpSessionLoop) -> None:
        """Push updated config options (modes, models) to the client."""
        await self.client.session_update(
            session_id=session.id,
            update=ConfigOptionUpdate(
                session_update="config_option_update",
                config_options=self._build_config_options(session),
            ),
        )

    async def _handle_leanstall(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        current = list(session.agent_loop.base_config.installed_agents)
        if "lean" in current:
            return await self._command_reply(
                session, "Lean agent is already installed.", message_id
            )

        VibeConfig.save_updates({"installed_agents": [*current, "lean"]})
        await self._reload_session_config(session)
        await self._send_config_option_update(session)
        return await self._command_reply(
            session,
            "Lean agent installed. Start a new session to switch to Lean mode.",
            message_id,
        )

    async def _handle_unleanstall(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        current = list(session.agent_loop.base_config.installed_agents)
        if "lean" not in current:
            return await self._command_reply(
                session, "Lean agent is not installed.", message_id
            )

        VibeConfig.save_updates({
            "installed_agents": [a for a in current if a != "lean"]
        })
        await self._reload_session_config(session)
        await self._send_config_option_update(session)
        return await self._command_reply(session, "Lean agent uninstalled.", message_id)

    async def _handle_data_retention(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        return await self._command_reply(session, DATA_RETENTION_MESSAGE, message_id)


def run_acp_server() -> None:
    try:
        asyncio.run(
            run_agent(
                agent=VibeAcpAgentLoop(),
                use_unstable_protocol=True,
                observers=[acp_message_observer],
            )
        )
    except KeyboardInterrupt:
        # This is expected when the server is terminated
        pass
    except Exception as e:
        # Log any unexpected errors
        print(f"ACP Agent Server error: {e}", file=sys.stderr)
        raise
