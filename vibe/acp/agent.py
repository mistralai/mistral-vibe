from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import signal
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
    SetSessionModeResponse,
    run_agent,
)
from acp.helpers import ContentBlock
from acp.schema import (
    AcpMcpServer,
    AgentCapabilities,
    AllowedOutcome,
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    ConfigOptionUpdate,
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
    SessionInfoUpdate,
    SessionListCapabilities,
    SessionModeState,
    SetSessionConfigOptionResponse,
    SseMcpServer,
    TerminalAuthMethod,
    ToolCallUpdate,
    Usage,
    UsageUpdate,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe import __version__
from vibe.acp.acp_logger import acp_message_observer
from vibe.acp.auth import (
    AcpAuthController,
    ApiKeyPersister,
    ApiKeyRemover,
    BrowserSignInServiceFactory,
    CredentialsPersister,
    OnboardingContextLoader,
    TenantDomainResolver,
)
from vibe.acp.commands import AcpCommandController, AcpCommandRegistry, InjectedPrompt
from vibe.acp.content import ProjectedPrompt, project_prompt
from vibe.acp.exceptions import (
    ConfigurationError,
    InternalError,
    InvalidRequestError,
    NotImplementedMethodError,
    SessionNotFoundError,
    UnauthenticatedError,
    from_public_error,
)
from vibe.acp.models import (
    ConfigSchemaResponse,
    ProjectLinksCreateRequest,
    ProjectLinksLinkRequest,
    ProjectLinksListRequest,
    ProjectLinksPickerLoadMoreRequest,
    ProjectLinksPickerLoadRequest,
    ProjectLinksResolveRootRequest,
    ProjectLinksUnlinkRequest,
)
from vibe.acp.session import AcpSession
from vibe.acp.session_updates import replay_session_updates, session_updates_for_event
from vibe.acp.tool_io import AcpClientToolHandler
from vibe.acp.user_display_content import (
    USER_DISPLAY_CONTENT_META_KEY,
    parse_user_display_content_metadata,
)
from vibe.acp.utils import (
    ToolOption,
    build_mode_state,
    build_model_config,
    build_permission_options,
    is_jetbrains_client,
    make_thinking_response,
)
from vibe.app_server._project_links import (
    ProjectLinksAuthError,
    ProjectLinksController,
    ProjectLinksInternalError,
    ProjectLinksInvalidRequest,
)
from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    StatsUpdated,
    TurnCompleted,
    TurnRetrying,
)
from vibe.app_server.host import AppServerHost
from vibe.app_server.local import (
    ClientDescriptor,
    LocalHarnessHost,
    LocalHarnessOptions,
    NewSessionIntent,
    ResumeSessionIntent,
)
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    ImageAttachment,
    MentionStats,
    PublicCallbackEntry,
    PublicRetryCategory,
    PublicTurnStatus,
    PublicTurnStopReason,
    TurnErrorCode,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities as AppServerClientCapabilities,
    ClientInfo,
    ClientToolCapability,
    ProtocolErrorCode,
    ReviewBaselineParams,
    ReviewHunksParams,
    ReviewMutationParams,
    ReviewStateParams,
    ReviewTurnDiffParams,
    SessionMCPHttpServer,
    SessionMCPServer,
    SessionMCPStdioServer,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.observability.logging import logger
from vibe.observability.sentry import capture_sentry_exception
from vibe.user_content import UserDisplayContent, UserResource

NON_INTERACTIVE_DISABLED_TOOLS = ("ask_user_question", "exit_plan_mode")
INITIAL_AVAILABLE_COMMANDS_DELAY_SECONDS = 0.1
type SessionStarter = Callable[[LocalHarnessOptions], Awaitable[AppServerSession]]


@dataclass(frozen=True, slots=True)
class _TurnInput:
    text: str
    client_message_id: str | None = None
    auto_title: str | None = None
    images: list[ImageAttachment] = field(default_factory=list)
    resources: list[UserResource] = field(default_factory=list)
    user_display_content: UserDisplayContent | None = None
    mention_stats: MentionStats | None = None
    injected: bool = False


def _project_acp_mcp_servers(
    servers: list[HttpMcpServer | SseMcpServer | McpServerStdio | AcpMcpServer],
) -> list[SessionMCPServer]:
    projected: list[SessionMCPServer] = []
    for server in servers:
        match server:
            case HttpMcpServer():
                projected.append(
                    SessionMCPHttpServer(
                        transport="streamable-http",
                        name=server.name,
                        url=server.url,
                        headers={
                            header.name: header.value for header in server.headers
                        },
                    )
                )
            case McpServerStdio():
                projected.append(
                    SessionMCPStdioServer(
                        name=server.name,
                        command=server.command,
                        args=server.args,
                        env={variable.name: variable.value for variable in server.env},
                    )
                )
            case SseMcpServer():
                raise ConfigurationError(
                    f"MCP server {server.name!r} uses unsupported SSE transport"
                )
            case AcpMcpServer():
                raise ConfigurationError(
                    f"MCP server {server.name!r} uses unsupported ACP transport"
                )
    return projected


class SessionSetTitleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    session_id: str = Field(alias="sessionId", min_length=1)
    title: str = Field(min_length=1)


class SessionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    session_id: str = Field(alias="sessionId", min_length=1)


class ForkSessionParams(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    message_id: str | None = Field(default=None, alias="messageId")


class TelemetryNotification(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    event: str
    properties: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(alias="sessionId")


# Bare name: `Client.ext_notification` prepends the `_` that ACP requires on
# extension methods, so the wire method is `_session/retrying` -- which is what
# the VS Code client subscribes to (see acp-session-retrying.ts). Adding the
# prefix here would emit `__session/retrying` and the retry would never arrive.
RETRYING_EXT_METHOD = "session/retrying"


class SessionRetryingNotification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(serialization_alias="sessionId")
    category: PublicRetryCategory
    detail: str


class VibeAcpAgent(AcpAgent):
    client: Client

    def __init__(
        self,
        *,
        session_starter: SessionStarter | None = None,
        onboarding_context_loader: OnboardingContextLoader | None = None,
        browser_sign_in_service_factory: BrowserSignInServiceFactory | None = None,
        api_key_persister: ApiKeyPersister | None = None,
        api_key_remover: ApiKeyRemover | None = None,
        credentials_persister: CredentialsPersister | None = None,
        tenant_domain_resolver: TenantDomainResolver | None = None,
        environ_before_dotenv_load: Mapping[str, str] | None = None,
    ) -> None:
        self.sessions: dict[str, AcpSession] = {}
        self.client_capabilities: ClientCapabilities | None = None
        self.client_info: Implementation | None = None
        self._harness_host = LocalHarnessHost()
        self._start_session = session_starter or self._harness_host.start
        self._passive_host: AppServerHost | None = None
        self._passive_host_lock = asyncio.Lock()
        auth_kwargs: dict[str, Any] = {
            "context_loader": onboarding_context_loader,
            "service_factory": browser_sign_in_service_factory,
            "environ_before_dotenv_load": environ_before_dotenv_load,
        }
        if api_key_persister is not None:
            auth_kwargs["api_key_persister"] = api_key_persister
        if api_key_remover is not None:
            auth_kwargs["api_key_remover"] = api_key_remover
        if credentials_persister is not None:
            auth_kwargs["credentials_persister"] = credentials_persister
        if tenant_domain_resolver is not None:
            auth_kwargs["tenant_domain_resolver"] = tenant_domain_resolver
        self._auth = AcpAuthController(**auth_kwargs)
        self._command_controller = AcpCommandController(
            lambda: self.client, self._send_config_options
        )

    @override
    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del protocol_version, kwargs
        self.client_capabilities = client_capabilities
        self.client_info = client_info
        delegated = bool(
            client_capabilities
            and client_capabilities.field_meta
            and client_capabilities.field_meta.get("browser-auth-delegated") is True
        )
        auth_methods: list[EnvVarAuthMethod | TerminalAuthMethod | Any] = [
            *self._auth.browser_methods(delegated=delegated)
        ]
        supports_terminal = bool(
            client_capabilities
            and client_capabilities.field_meta
            and client_capabilities.field_meta.get("terminal-auth") is True
        )
        if supports_terminal:
            command = sys.executable
            args = (
                ["--setup"]
                if "python" not in Path(command).name
                else [sys.argv[0], "--setup"]
            )
            auth_methods.append(
                TerminalAuthMethod(
                    type="terminal",
                    id="vibe-setup",
                    name="Register your API Key",
                    description="Register your API Key inside Mistral Vibe",
                    args=args,
                    field_meta={
                        "terminal-auth": {
                            "command": command,
                            "args": args,
                            "label": "Mistral Vibe Setup",
                        }
                    },
                )
            )
        if (
            is_jetbrains_client(client_info)
            and self._auth.status().can_use_active_provider
        ):
            auth_methods = []
        return InitializeResponse(
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    audio=False, embedded_context=True, image=True
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
            auth_methods=cast(Any, auth_methods),
        )

    @override
    async def authenticate(
        self, method_id: str, **kwargs: Any
    ) -> AuthenticateResponse | None:
        return await self._auth.authenticate(method_id, kwargs)

    @override
    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio | AcpMcpServer]
        | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del kwargs
        session = await self._create_session(
            Path(cwd),
            NewSessionIntent(),
            workspace_roots=additional_directories,
            mcp_servers=mcp_servers,
        )
        modes, _ = self._mode_state(session)
        self._send_usage_update(session)
        return NewSessionResponse(
            session_id=session.id,
            modes=modes,
            config_options=self._config_options(session),
            field_meta=await self._trust_meta(session, cwd),
        )

    @override
    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio | AcpMcpServer]
        | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        del kwargs
        session = await self._create_session(
            Path(cwd),
            ResumeSessionIntent(session_id),
            acp_session_id=session_id,
            workspace_roots=additional_directories,
            mcp_servers=mcp_servers,
        )
        await self._load_complete_history(session)
        for update in replay_session_updates(session.app_server.state):
            await self.client.session_update(session_id=session.id, update=update)
        self._send_usage_update(session)
        modes, _ = self._mode_state(session)
        return LoadSessionResponse(
            modes=modes,
            config_options=self._config_options(session),
            field_meta=await self._trust_meta(session, cwd),
        )

    async def _create_session(
        self,
        cwd: Path,
        intent: NewSessionIntent | ResumeSessionIntent,
        *,
        acp_session_id: str | None = None,
        workspace_roots: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio | AcpMcpServer]
        | None = None,
    ) -> AcpSession:
        client_tool_handler = AcpClientToolHandler(self.client)
        try:
            app_server = await self._start_session(
                LocalHarnessOptions(
                    client=self._client_descriptor(),
                    session_options=SessionOptions(
                        cwd=str(cwd),
                        workspace_roots=workspace_roots or [],
                        disabled_tools=list(NON_INTERACTIVE_DISABLED_TOOLS),
                        mcp_servers=_project_acp_mcp_servers(mcp_servers or []),
                    ),
                    session=intent,
                    client_tool_handler=client_tool_handler,
                )
            )
        except AppServerResponseError as exc:
            if exc.error.code is ProtocolErrorCode.UNAUTHORIZED:
                data = exc.error.data
                provider = data.get("provider") if isinstance(data, dict) else None
                if isinstance(provider, str):
                    raise UnauthenticatedError.for_provider(provider) from exc
            if exc.error.code is ProtocolErrorCode.NOT_FOUND and isinstance(
                intent, ResumeSessionIntent
            ):
                raise SessionNotFoundError(intent.session_id) from exc
            raise ConfigurationError(exc.error.message) from exc
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        session_id = acp_session_id or app_server.session_id
        client_tool_handler.bind_session(session_id)
        commands = AcpCommandRegistry(
            vibe_code_enabled=app_server.resources.config.current.vibe_code_enabled
        )
        session = AcpSession(
            session_id=session_id,
            app_server=app_server,
            cwd=cwd.resolve(),
            commands=commands,
        )
        self.sessions[session.id] = session
        session.spawn(self._forward_unsolicited_events(session))
        session.spawn(self._warm_up(session))
        session.spawn(self._send_initial_commands(session))
        return session

    @staticmethod
    async def _load_complete_history(session: AcpSession) -> None:
        while before := session.app_server.resources.sessions.history_before_cursor:
            page = await session.app_server.resources.sessions.load_before(
                before, limit=500
            )
            if not page.data:
                raise RuntimeError("History pagination did not advance")

    def _client_descriptor(self) -> ClientDescriptor:
        info = self.client_info
        capabilities = self.client_capabilities
        client_tools: list[ClientToolCapability] = []
        if capabilities is not None and capabilities.fs is not None:
            if capabilities.fs.read_text_file:
                client_tools.append("filesystem/read")
            if capabilities.fs.write_text_file:
                client_tools.append("filesystem/write")
        if capabilities is not None and capabilities.terminal:
            client_tools.append("terminal")
        return ClientDescriptor(
            info=ClientInfo(
                name=info.name if info is not None else "vibe_acp_client",
                title=info.title if info is not None else None,
                version=info.version if info is not None else "unknown",
                entrypoint="acp",
            ),
            capabilities=AppServerClientCapabilities(
                callback_kinds=["approval"], client_tools=client_tools
            ),
        )

    async def _warm_up(self, session: AcpSession) -> None:
        with suppress(Exception):
            await session.app_server.resources.runtime.wait_until_ready()
            await self._notify_mcp_discovery_failures(session)
            await self._notify_mcp_auth(session)

    async def _notify_mcp_discovery_failures(self, session: AcpSession) -> None:
        errors = session.app_server.resources.runtime.mcp.discovery_errors
        if not errors:
            return
        lines = ["The following MCP servers failed to connect:"]
        lines.extend(f"- {name}: {error}" for name, error in sorted(errors.items()))
        await self._command_controller.message(session, "\n".join(lines))

    async def _notify_mcp_auth(self, session: AcpSession) -> None:
        aliases = session.app_server.resources.runtime.mcp.needs_auth
        if not aliases:
            return
        message = (
            "MCP OAuth login is required for: "
            f"{', '.join(aliases)}. Run `/mcp login <alias>` in Vibe."
        )
        await self._command_controller.message(session, message)

    async def _forward_unsolicited_events(self, session: AcpSession) -> None:
        async for event in session.app_server.events():
            await self._forward_event(session, event)
            if isinstance(event, TurnCompleted):
                self._send_usage_update(session)

    async def _forward_event(self, session: AcpSession, event: AppServerEvent) -> None:
        if isinstance(event, CallbackRequested):
            await self._answer_callback(session, event.callback)
            return
        if isinstance(event, TurnRetrying):
            await self._send_retrying(session, event)
            return
        for update in session_updates_for_event(event):
            await self.client.session_update(session_id=session.id, update=update)
        if isinstance(event, StatsUpdated):
            self._send_usage_update(session)

    async def _send_retrying(self, session: AcpSession, event: TurnRetrying) -> None:
        notification = SessionRetryingNotification(
            session_id=session.id,
            category=event.params.category,
            detail=event.params.detail,
        )
        await self.client.ext_notification(
            RETRYING_EXT_METHOD, notification.model_dump(mode="json", by_alias=True)
        )

    @staticmethod
    async def _prepare_turn_input(
        session: AcpSession,
        content: ProjectedPrompt,
        *,
        message_id: str,
        display: UserDisplayContent | None,
    ) -> _TurnInput:
        prepared = await session.app_server.resources.workspace.prepare_prompt(
            content.text, title_content=content.title_content
        )
        return _TurnInput(
            text=prepared.prompt_text,
            client_message_id=message_id,
            auto_title=prepared.auto_title,
            images=[*prepared.images, *content.images],
            resources=content.resources,
            user_display_content=display,
            mention_stats=prepared.mentions,
        )

    @override
    async def prompt(
        self, session_id: str, prompt: list[ContentBlock], **kwargs: Any
    ) -> PromptResponse:
        session = self._get_session(session_id)
        try:
            display = parse_user_display_content_metadata(
                kwargs.get(USER_DISPLAY_CONTENT_META_KEY)
            )
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid user display content metadata: {exc}"
            ) from exc
        content = project_prompt(prompt)
        text = content.text
        message_id = str(uuid4())
        match await self._command_controller.execute(session, text, message_id):
            case PromptResponse() as response:
                return response
            case InjectedPrompt(text=injected_text):
                turn_input = _TurnInput(text=injected_text, injected=True)
            case None:
                self._record_skill_command(session, text)
                turn_input = await self._prepare_turn_input(
                    session, content, message_id=message_id, display=display
                )

        async def run_turn() -> None:
            async with aclosing(
                session.app_server.act(
                    turn_input.text,
                    client_message_id=turn_input.client_message_id,
                    auto_title=turn_input.auto_title,
                    images=turn_input.images,
                    resources=turn_input.resources,
                    user_display_content=turn_input.user_display_content,
                    mention_stats=turn_input.mention_stats,
                    injected=turn_input.injected,
                )
            ) as events:
                async for event in events:
                    await self._forward_event(session, event)

        try:
            await run_turn()
        except asyncio.CancelledError:
            self._send_usage_update(session)
            return PromptResponse(stop_reason="cancelled", usage=self._usage(session))
        except AppServerTurnError as exc:
            if exc.error.code == TurnErrorCode.RESPONSE_TOO_LONG:
                self._send_usage_update(session)
                return PromptResponse(
                    stop_reason="max_tokens", usage=self._usage(session)
                )
            raise from_public_error(exc.error) from exc
        except Exception as exc:
            capture_sentry_exception(
                exc,
                fatal=False,
                tags={
                    "vibe_boundary": "acp_request_handler",
                    "acp_method": "session/prompt",
                },
            )
            raise InternalError(str(exc)) from exc
        self._send_usage_update(session)
        turn = next(reversed(session.app_server.state.turns or []), None)
        if turn is not None and turn.status is PublicTurnStatus.INTERRUPTED:
            return PromptResponse(stop_reason="cancelled", usage=self._usage(session))
        if turn is not None and turn.stop_reason is PublicTurnStopReason.LIMIT:
            return PromptResponse(
                stop_reason="max_turn_requests", usage=self._usage(session)
            )
        show_feedback = await session.app_server.resources.feedback.should_show(
            pending_user_messages=1
        )
        if show_feedback:
            await session.app_server.resources.feedback.record("asked")
        return PromptResponse(
            stop_reason="end_turn",
            usage=self._usage(session),
            field_meta={"show_feedback_prompt": True} if show_feedback else None,
        )

    async def _answer_callback(
        self, session: AcpSession, callback: PublicCallbackEntry
    ) -> None:
        if not isinstance(callback.detail, ApprovalCallbackDetail):
            await session.app_server.deny_callback(callback)
            return
        response = await self.client.request_permission(
            session_id=session.id,
            tool_call=ToolCallUpdate(
                tool_call_id=callback.detail.related_entry_id
                or callback.detail.effect.tool_name
            ),
            options=build_permission_options(callback.detail.required_permissions),
        )
        decision = ApprovalDecisionType.DENY
        feedback: str | None = None
        if isinstance(response.outcome, AllowedOutcome):
            match response.outcome.option_id:
                case ToolOption.ALLOW_ONCE:
                    decision = ApprovalDecisionType.APPROVE
                case ToolOption.ALLOW_ALWAYS:
                    decision = ApprovalDecisionType.APPROVE_FOR_SESSION
                case ToolOption.ALLOW_ALWAYS_PERMANENT:
                    decision = ApprovalDecisionType.APPROVE_PERMANENTLY
                case ToolOption.REJECT_ONCE:
                    session.app_server.resources.telemetry.record(
                        "vibe.user_cancelled_action", {"action": "reject_approval"}
                    )
                    feedback = (
                        "User rejected the tool call; provide an alternative plan"
                    )
        await session.app_server.respond_to_callback(
            callback.callback_id,
            ApprovalCallbackOutput(
                decision=ApprovalDecision(type=decision), feedback=feedback
            ),
        )

    @override
    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        session = self._get_session(session_id)
        session.app_server.resources.telemetry.record(
            "vibe.user_cancelled_action", {"action": "interrupt_agent"}
        )
        await session.cancel_prompt()

    @override
    async def close_session(
        self, session_id: str, **kwargs: Any
    ) -> CloseSessionResponse | None:
        del kwargs
        if not await self._close_session_if_present(session_id):
            raise SessionNotFoundError(session_id)
        return CloseSessionResponse()

    async def _close_session_if_present(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        await session.close()
        self.sessions.pop(session_id, None)
        return True

    async def close(self) -> None:
        sessions = list(self.sessions.values())
        self.sessions.clear()
        if sessions:
            await asyncio.gather(
                *(session.close() for session in sessions), return_exceptions=True
            )
        if self._passive_host is not None:
            await self._passive_host.close()
            self._passive_host = None
        await self._harness_host.close()

    @override
    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        del cursor, kwargs
        saved = await (await self._host_resources()).list_sessions(cwd=cwd)
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    session_id=item.id,
                    cwd=item.cwd or "",
                    # No LLM title yet: fall back to the first-message preview so
                    # the list stays readable instead of showing "untitled".
                    title=item.title or item.preview,
                    updated_at=datetime.fromtimestamp(
                        item.updated_at / 1000, UTC
                    ).isoformat(),
                )
                for item in saved
            ]
        )

    @override
    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        del kwargs
        session = self._get_session(session_id)
        if not self._is_primary_mode(session, mode_id):
            return None
        await session.app_server.resources.agents.switch(mode_id)
        return SetSessionModeResponse()

    @override
    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> SetSessionConfigOptionResponse:
        del kwargs
        session = self._get_session(session_id)
        try:
            match config_id, value:
                case "mode", str(mode) if self._is_primary_mode(session, mode):
                    await session.app_server.resources.agents.switch(mode)
                case "model", str(model) if any(
                    candidate.alias == model
                    for candidate in session.app_server.resources.config.current.models
                ):
                    await session.app_server.resources.config.update(
                        {"active_model": model}, reload_runtime=True
                    )
                case "thinking", str(level) if level in {
                    "off",
                    "low",
                    "medium",
                    "high",
                    "max",
                }:
                    await session.app_server.resources.config.set_thinking(
                        cast(Any, level)
                    )
                case "max_turns", str(raw):
                    await session.app_server.resources.sessions.update_settings(
                        max_turns=int(raw)
                    )
                case "max_tokens", str(raw):
                    await session.app_server.resources.sessions.update_settings(
                        max_tokens=int(raw)
                    )
                case _:
                    raise InvalidRequestError(
                        f"Unsupported config option {config_id}={value!r}"
                    )
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        except AppServerResponseError as exc:
            raise InvalidRequestError(exc.error.message) from exc
        return SetSessionConfigOptionResponse(
            config_options=self._config_options(session)
        )

    @override
    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio | AcpMcpServer]
        | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        source = self._get_session(session_id)
        try:
            params = ForkSessionParams.model_validate(kwargs)
            fork = await source.app_server.resources.sessions.fork(
                params.message_id, attach=False
            )
        except ValidationError as exc:
            raise InvalidRequestError(f"Invalid fork parameters: {exc}") from exc
        except AppServerResponseError as exc:
            raise InvalidRequestError(exc.error.message) from exc
        child = await self._create_session(
            Path(cwd),
            ResumeSessionIntent(fork.state.session.id),
            acp_session_id=fork.state.session.id,
            workspace_roots=additional_directories,
            mcp_servers=mcp_servers,
        )
        modes, _ = self._mode_state(child)
        self._send_usage_update(child)
        return ForkSessionResponse(
            session_id=child.id, modes=modes, config_options=self._config_options(child)
        )

    @override
    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio | AcpMcpServer]
        | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        del kwargs
        if session_id not in self.sessions:
            session = await self._create_session(
                Path(cwd),
                ResumeSessionIntent(session_id),
                acp_session_id=session_id,
                workspace_roots=additional_directories,
                mcp_servers=mcp_servers,
            )
        else:
            session = self._get_session(session_id)
        self._send_usage_update(session)
        return ResumeSessionResponse()

    @override
    async def ext_method(self, method: str, params: dict) -> dict:
        match method:
            case "auth/status":
                state = self._auth.status()
                result = {
                    "authenticated": state.can_use_active_provider,
                    "authState": state.kind.value,
                    "signOutAvailable": state.sign_out_available,
                    "customDomain": self._auth.custom_domain(),
                }
            case "auth/signOut":
                self._auth.sign_out()
                result = {}
            case "config/schema":
                result = await self._config_schema()
            case "session/set_title":
                try:
                    request = SessionSetTitleRequest.model_validate(params)
                except ValidationError as exc:
                    raise InvalidRequestError(
                        f"Invalid ACP session title request: {exc}"
                    ) from exc
                session = self._find_live_session(request.session_id)
                try:
                    response = (
                        await session.app_server.resources.sessions.rename_with_metadata(
                            request.title
                        )
                        if session is not None
                        else await (await self._host_resources()).rename_session(
                            request.session_id, request.title
                        )
                    )
                except AppServerResponseError as exc:
                    if exc.error.code is ProtocolErrorCode.NOT_FOUND:
                        raise SessionNotFoundError(request.session_id) from exc
                    raise InvalidRequestError(exc.error.message) from exc
                await self.client.session_update(
                    session_id=session.id
                    if session is not None
                    else request.session_id,
                    update=SessionInfoUpdate(
                        session_update="session_info_update",
                        title=response.title,
                        updated_at=response.updated_at,
                    ),
                )
                result = {}
            case "session/delete":
                try:
                    request = SessionDeleteRequest.model_validate(params)
                except ValidationError as exc:
                    raise InvalidRequestError(
                        f"Invalid ACP session delete request: {exc}"
                    ) from exc
                await self._delete_session(request.session_id)
                result = {}
            case "trust/status" | "trust/decision":
                result = await self._trust_extension(method, params)
            case "rewind/preview" | "rewind/to":
                result = await self._rewind_extension(method, params)
            case (
                "review/state"
                | "review/baseline"
                | "review/turnDiff"
                | "review/hunks"
                | "review/approve"
                | "review/revert"
            ):
                result = await self._review_extension(method, params)
            case _ if method.startswith("projectLinks/"):
                result = await self._project_links_extension(method, params)
            case _:
                raise NotImplementedMethodError(method)
        return result

    async def _config_schema(self) -> dict[str, Any]:
        response = await (await self._host_resources()).read_config_schema()
        return ConfigSchemaResponse(
            version=response.config_schema_version, schema=response.config_schema
        ).model_dump(mode="json", by_alias=True)

    # -- projectLinks ----------------------------------------------------------
    #
    # The ACP runtime must not import vibe.core, so the projectLinks lifecycle
    # lives in the app-server ProjectLinksController. These handlers only
    # validate params, delegate, and map controller errors to ACP errors.

    @staticmethod
    def _project_links_request[RequestT: BaseModel](
        model: type[RequestT], params: dict
    ) -> RequestT:
        try:
            return model.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP project links request: {exc}"
            ) from exc

    async def _project_links_extension(
        self, method: str, params: dict
    ) -> dict[str, Any]:
        controller = ProjectLinksController()
        try:
            match method:
                case "projectLinks/list":
                    self._project_links_request(ProjectLinksListRequest, params)
                    result = await controller.list_links()
                case "projectLinks/resolveRoot":
                    resolve = self._project_links_request(
                        ProjectLinksResolveRootRequest, params
                    )
                    result = await controller.resolve_root(resolve.root_path)
                case "projectLinks/picker/load":
                    load = self._project_links_request(
                        ProjectLinksPickerLoadRequest, params
                    )
                    result = await controller.picker_load(load.root_path)
                case "projectLinks/picker/loadMore":
                    load_more = self._project_links_request(
                        ProjectLinksPickerLoadMoreRequest, params
                    )
                    result = await controller.picker_load_more(
                        load_more.root_path, load_more.cursor
                    )
                case "projectLinks/create":
                    create = self._project_links_request(
                        ProjectLinksCreateRequest, params
                    )
                    result = await controller.create(
                        create.root_path, create.name, create.default_branch
                    )
                case "projectLinks/link":
                    link = self._project_links_request(ProjectLinksLinkRequest, params)
                    result = await controller.link(
                        link.root_path, link.project_id, link.project_name
                    )
                case "projectLinks/unlink":
                    unlink = self._project_links_request(
                        ProjectLinksUnlinkRequest, params
                    )
                    result = await controller.unlink(unlink.root_path)
                case _:
                    raise NotImplementedMethodError(method)
        except ProjectLinksAuthError as exc:
            raise UnauthenticatedError(str(exc)) from exc
        except ProjectLinksInvalidRequest as exc:
            raise InvalidRequestError(str(exc)) from exc
        except ProjectLinksInternalError as exc:
            raise InternalError(str(exc)) from exc
        return result

    async def _delete_session(self, session_id: str) -> None:
        session = self._find_live_session(session_id)
        if session is None:
            await (await self._host_resources()).delete_session(session_id)
            return

        saved_session_id = session.app_server.exit_summary().session_id
        await session.close()
        if saved_session_id is not None:
            await (await self._host_resources()).delete_session(saved_session_id)
        self.sessions.pop(session.id, None)

    @override
    async def ext_notification(self, method: str, params: dict) -> None:
        if method != "telemetry/send":
            return
        try:
            notification = TelemetryNotification.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP telemetry notification: {exc}"
            ) from exc
        session = self.sessions.get(notification.session_id)
        if session is None:
            return
        match notification.event:
            case "vibe.at_mention_inserted":
                session.app_server.resources.telemetry.record(
                    notification.event, notification.properties
                )
            case "vibe.user_rating_feedback":
                session.app_server.resources.telemetry.record(
                    notification.event,
                    {
                        "rating": notification.properties.get("rating", 0),
                        "model": session.app_server.resources.config.current.active_model.alias,
                    },
                    correlate_last_request=True,
                )
            case _:
                logger.warning(
                    "Ignoring unsupported ACP telemetry event: %s", notification.event
                )

    def on_connect(self, conn: Client) -> None:
        self.client = conn

    async def _host_resources(self) -> AppServerHost:
        if self._passive_host is not None:
            return self._passive_host
        async with self._passive_host_lock:
            if self._passive_host is None:
                self._passive_host = await self._harness_host.connect(
                    LocalHarnessOptions(
                        client=self._client_descriptor(),
                        session_options=SessionOptions(cwd=str(Path.cwd())),
                    )
                )
        return self._passive_host

    async def _trust_extension(self, method: str, params: dict[str, Any]) -> dict:
        requested_session = params.get("sessionId") or params.get("session_id")
        session = (
            self._get_session(requested_session)
            if isinstance(requested_session, str)
            else None
        )
        if session is not None:
            if method == "trust/status":
                response = await session.app_server.resources.workspace.trust_status(
                    params.get("cwd")
                )
            else:
                decision = params.get("decision")
                if decision not in {"trust_repo", "trust_cwd", "decline"}:
                    raise InvalidRequestError(f"Unknown trust decision: {decision}")
                response = await session.app_server.resources.workspace.decide_trust(
                    cast(Any, decision), cwd=params.get("cwd")
                )
        else:
            host = await self._host_resources()
            if method == "trust/status":
                response = await host.trust_status(params.get("cwd"))
            else:
                decision = params.get("decision")
                if decision not in {"trust_repo", "trust_cwd", "decline"}:
                    raise InvalidRequestError(f"Unknown trust decision: {decision}")
                response = await host.decide_trust(
                    cast(Any, decision), cwd=params.get("cwd")
                )
        return {
            "trust_status": response.status,
            "details": (
                response.details.model_dump(mode="json", by_alias=True)
                if response.details is not None
                else None
            ),
        }

    async def _rewind_extension(self, method: str, params: dict[str, Any]) -> dict:
        session_id = params.get("sessionId") or params.get("session_id")
        entry_id = params.get("messageId") or params.get("message_id")
        if not isinstance(session_id, str) or not isinstance(entry_id, str):
            raise InvalidRequestError("Rewind requires sessionId and messageId")
        session = self._get_session(session_id)
        if method == "rewind/preview":
            try:
                paths = await session.app_server.resources.sessions.rewind_preview(
                    entry_id
                )
            except AppServerResponseError as exc:
                raise InvalidRequestError(exc.error.message) from exc
            return {"paths": paths}
        try:
            response = await session.app_server.resources.sessions.rewind(
                entry_id,
                restore_files=bool(params.get("restoreFiles", True)),
                inplace=True,
            )
        except AppServerResponseError as exc:
            raise InvalidRequestError(exc.error.message) from exc
        return {
            "messageContent": response.message,
            "restoreErrors": list(response.restore_errors),
            "restoredPaths": list(response.restored_paths),
        }

    async def _review_extension(self, method: str, params: dict[str, Any]) -> dict:
        try:
            match method:
                case "review/state":
                    request = ReviewStateParams.model_validate(params)
                    review = self._get_session(
                        request.session_id
                    ).app_server.resources.review
                    response = await review.state()
                case "review/baseline":
                    request = ReviewBaselineParams.model_validate(params)
                    review = self._get_session(
                        request.session_id
                    ).app_server.resources.review
                    response = await review.baseline(request.path)
                case "review/turnDiff":
                    request = ReviewTurnDiffParams.model_validate(params)
                    review = self._get_session(
                        request.session_id
                    ).app_server.resources.review
                    response = await review.turn_diff(request.path, request.owner)
                case "review/hunks":
                    request = ReviewHunksParams.model_validate(params)
                    review = self._get_session(
                        request.session_id
                    ).app_server.resources.review
                    response = await review.hunks(request.path, request.owner)
                case "review/approve" | "review/revert":
                    request = ReviewMutationParams.model_validate(params)
                    review = self._get_session(
                        request.session_id
                    ).app_server.resources.review
                    try:
                        if method == "review/approve":
                            await review.approve(request.target)
                        else:
                            await review.revert(request.target)
                    except AppServerResponseError as exc:
                        raise InvalidRequestError(exc.error.message) from exc
                    return {}
                case _:
                    raise NotImplementedMethodError(method)
        except ValidationError as exc:
            raise InvalidRequestError(f"Invalid ACP {method} request: {exc}") from exc
        return response.model_dump(mode="json", by_alias=True)

    async def _trust_meta(self, session: AcpSession, cwd: str) -> dict[str, Any]:
        response = await session.app_server.resources.workspace.trust_status(cwd)
        return {
            "workspace_trust": {
                "status": response.status,
                "details": (
                    response.details.model_dump(mode="json", by_alias=True)
                    if response.details is not None
                    else None
                ),
            }
        }

    def _mode_state(
        self, session: AcpSession
    ) -> tuple[SessionModeState, SessionConfigOptionSelect]:
        agents = session.app_server.resources.agents
        return build_mode_state(agents.all, agents.active)

    def _is_primary_mode(self, session: AcpSession, mode_id: str) -> bool:
        modes, _ = self._mode_state(session)
        return any(mode.id == mode_id for mode in modes.available_modes)

    @staticmethod
    def _record_skill_command(session: AcpSession, text: str) -> None:
        command, _, _ = text.strip().partition(" ")
        if not command.startswith("/"):
            return
        name = command[1:].casefold()
        skill = next(
            (
                candidate
                for candidate in session.app_server.resources.runtime.skills
                if candidate.user_invocable and candidate.name.casefold() == name
            ),
            None,
        )
        if skill is None:
            return
        session.app_server.resources.telemetry.record(
            "vibe.slash_command_used", {"command": skill.name, "command_type": "skill"}
        )

    def _config_options(
        self, session: AcpSession
    ) -> list[SessionConfigOptionSelect | SessionConfigOptionBoolean]:
        _, mode = self._mode_state(session)
        config = session.app_server.resources.config.current
        return [mode, build_model_config(config), make_thinking_response(config)]

    def _usage(self, session: AcpSession) -> Usage:
        usage = session.app_server.resources.runtime.stats.token_usage
        return Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        )

    def _send_usage_update(self, session: AcpSession) -> None:
        async def send() -> None:
            runtime = session.app_server.resources.runtime
            stats = runtime.stats
            cost = (
                Cost(amount=stats.session_cost, currency="USD")
                if stats.session_cost > 0
                else None
            )
            await self.client.session_update(
                session_id=session.id,
                update=UsageUpdate(
                    session_update="usage_update",
                    used=stats.context_tokens,
                    size=runtime.context_window,
                    cost=cost,
                ),
            )

        session.spawn(send())

    async def _send_initial_commands(self, session: AcpSession) -> None:
        await asyncio.sleep(INITIAL_AVAILABLE_COMMANDS_DELAY_SECONDS)
        await self._command_controller.send_commands(session)

    async def _send_config_options(self, session: AcpSession) -> None:
        await self.client.session_update(
            session_id=session.id,
            update=ConfigOptionUpdate(
                session_update="config_option_update",
                config_options=self._config_options(session),
            ),
        )

    def _get_session(self, session_id: str) -> AcpSession:
        if session := self.sessions.get(session_id):
            return session
        raise SessionNotFoundError(session_id)

    def _find_live_session(self, session_id: str) -> AcpSession | None:
        return self.sessions.get(session_id) or next(
            (
                session
                for session in self.sessions.values()
                if session.app_server.session_id == session_id
            ),
            None,
        )


async def _serve_acp_agent(agent: VibeAcpAgent) -> None:
    try:
        await run_agent(
            agent=agent, use_unstable_protocol=True, observers=[acp_message_observer]
        )
    finally:
        await agent.close()


def run_acp_server(
    *, environ_before_dotenv_load: Mapping[str, str] | None = None
) -> None:
    agent = VibeAcpAgent(environ_before_dotenv_load=environ_before_dotenv_load)
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        asyncio.run(_serve_acp_agent(agent))
    except KeyboardInterrupt:
        with suppress(Exception):
            asyncio.run(asyncio.wait_for(agent.close(), timeout=1.0))
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
