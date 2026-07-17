from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC
from functools import lru_cache
import logging
import os
from pathlib import Path
import signal
import sys
from typing import Any, Protocol, cast, override
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
from acp.helpers import ContentBlock, SessionUpdate, update_available_commands
from acp.schema import (
    AcpMcpServer,
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
    SessionInfoUpdate,
    SessionListCapabilities,
    SetSessionConfigOptionResponse,
    SseMcpServer,
    TerminalAuthMethod,
    TerminalToolCallContent,
    TextContentBlock,
    TextResourceContents,
    ToolCallProgress,
    ToolCallUpdate,
    UnstructuredCommandInput,
    Usage,
    UsageUpdate,
    UserMessageChunk,
)
from keyring.errors import KeyringError
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from vibe import VIBE_ROOT, __version__
from vibe.acp.acp_logger import acp_message_observer
from vibe.acp.commands import AcpCommand, AcpCommandContext, AcpCommandRegistry
from vibe.acp.exceptions import (
    CompactionError,
    ConfigurationError,
    ContextTooLongError,
    ConversationLimitError,
    ImagesNotSupportedError as AcpImagesNotSupportedError,
    InternalError,
    InvalidRequestError,
    NotImplementedMethodError,
    RateLimitError,
    RefusalError,
    SessionLoadError,
    SessionNotFoundError,
    UnauthenticatedError,
)
from vibe.acp.image_blocks import extract_image_attachments
from vibe.acp.models import ConfigSchemaResponse
from vibe.acp.session import AcpSessionLoop
from vibe.acp.teleport import handle_teleport_command
from vibe.acp.title import acp_blocks_to_title_segments
from vibe.acp.tools.base import BaseAcpTool
from vibe.acp.tools.events import ToolTerminalOpenedEvent
from vibe.acp.tools.session_update import (
    resolve_kind,
    tool_call_session_update,
    tool_result_session_update,
)
from vibe.acp.user_display_content import (
    USER_DISPLAY_CONTENT_META_KEY,
    parse_user_display_content_metadata,
)
from vibe.acp.utils import (
    THINKING_LEVELS,
    ThinkingLevel,
    ToolOption,
    build_mode_state,
    build_model_config,
    build_permission_options,
    create_assistant_message_replay,
    create_compact_end_session_update,
    create_compact_start_session_update,
    create_reasoning_replay,
    create_tool_result_replay,
    create_user_message_replay,
    get_proxy_help_text,
    is_jetbrains_client,
    is_valid_acp_mode,
    make_thinking_response,
    tool_call_replay_update,
)
from vibe.core.agent_loop import (
    AgentLoop,
    CompactionFailedError,
    ImagesNotSupportedError,
)
from vibe.core.agents.models import CHAT as CHAT_AGENT
from vibe.core.auth import MCPOAuthError
from vibe.core.cache_store import FileSystemVibeCodeCacheStore
from vibe.core.config import (
    AnyVibeConfig,
    MissingAPIKeyError,
    ProviderConfig,
    SessionLoggingConfig,
    VibeConfig,
    VibeConfigSchema,
    load_dotenv_values,
)
from vibe.core.config.orchestrator_legacy import LegacyConfigOrchestrator
from vibe.core.config.patch import escape_json_pointer_token
from vibe.core.data_retention import DATA_RETENTION_MESSAGE
from vibe.core.feedback import record_feedback_asked, should_show_feedback
from vibe.core.hooks.config import load_hooks_from_fs
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.proxy_setup import (
    ProxySetupError,
    parse_proxy_command,
    set_proxy_var,
    unset_proxy_var,
)
from vibe.core.rewind import RewindError
from vibe.core.sentry import capture_sentry_exception
from vibe.core.session.saved_sessions import (
    delete_saved_session,
    update_saved_session_title,
    update_saved_session_title_at_path,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.session.title_format import format_session_title
from vibe.core.telemetry.build_metadata import build_launch_context
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext
from vibe.core.tools.mcp import AuthStatus
from vibe.core.tools.permissions import RequiredPermission
from vibe.core.trusted_folders import (
    WorkspaceTrustDecision,
    WorkspaceTrustPrompt,
    WorkspaceTrustStatus,
    apply_workspace_trust_decision,
    available_workspace_trust_decisions,
    maybe_build_workspace_trust_prompt,
    trusted_folders_manager,
)
from vibe.core.types import (
    AgentProfileChangedEvent,
    ApprovalCallback,
    ApprovalResponse,
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    ContextTooLongError as CoreContextTooLongError,
    ImageAttachment,
    LLMMessage,
    RateLimitError as CoreRateLimitError,
    ReasoningEvent,
    RefusalError as CoreRefusalError,
    ResponseTooLongError as CoreResponseTooLongError,
    Role,
    SessionTitleUpdatedEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserDisplayContentMetadata,
    UserMessageEvent,
)
from vibe.core.utils import (
    CancellationReason,
    ConversationLimitException,
    get_user_cancellation_message,
)
from vibe.setup.auth import (
    AuthState,
    AuthStateKind,
    BrowserSignInAttempt,
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInService,
    HttpBrowserSignInGateway,
    assess_auth_state,
)
from vibe.setup.auth.api_key_persistence import (
    persist_api_key,
    remove_api_key,
    resolve_api_key_provider,
)
from vibe.setup.onboarding.context import OnboardingContext

logger = logging.getLogger("vibe")


NON_INTERACTIVE_DISABLED_TOOLS = ["ask_user_question", "exit_plan_mode"]
INITIAL_AVAILABLE_COMMANDS_DELAY_SECONDS = 0.1
_MCP_COMMAND_MAX_SPLITS = 2
_MCP_COMMAND_ARG_INDEX = 2
WORKSPACE_TRUST_META_KEY = "workspace_trust"
TRUST_GRANT_DECISIONS = {
    WorkspaceTrustDecision.TRUST_REPO,
    WorkspaceTrustDecision.TRUST_CWD,
}


def _mcp_tui_login_message(alias: str) -> str:
    return (
        "MCP OAuth login must be performed via the Vibe TUI. "
        "Run `vibe` in a terminal and execute "
        f"`/mcp login {alias}` there. Tokens are stored in the OS keyring; "
        "run `/reload` in this ACP session afterward if the tools are not visible."
    )


def _merge_non_interactive_disabled_tools(config: AnyVibeConfig) -> None:
    for tool in NON_INTERACTIVE_DISABLED_TOOLS:
        if tool not in config.disabled_tools:
            config.disabled_tools.append(tool)


class ForkSessionParams(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: str | None = Field(default=None, alias="messageId")


class SessionSetTitleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    session_id: str = Field(
        validation_alias=AliasChoices("session_id", "sessionId"), min_length=1
    )
    title: str = Field(min_length=1)


class SessionDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    session_id: str = Field(
        validation_alias=AliasChoices("session_id", "sessionId"), min_length=1
    )


class TelemetrySendNotification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str
    properties: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(validation_alias=AliasChoices("session_id", "sessionId"))


class WorkspaceTrustDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    cwd: str
    repo_root: str | None = Field(default=None, alias="repoRoot")
    ignored_files: list[str] = Field(alias="ignoredFiles")
    available_decisions: list[WorkspaceTrustDecision] = Field(
        alias="availableDecisions"
    )


class WorkspaceTrustStatusRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    cwd: str | None = None


class WorkspaceTrustDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cwd: str | None = None
    decision: WorkspaceTrustDecision
    session_id: str | None = Field(
        default=None, validation_alias=AliasChoices("session_id", "sessionId")
    )


class WorkspaceTrustStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    trust_status: WorkspaceTrustStatus = Field(alias="trust_status")
    details: WorkspaceTrustDetails | None = None


class RewindPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    message_id: str = Field(alias="messageId")


class RewindPreviewResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    paths: list[str]


class RewindToRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    message_id: str = Field(alias="messageId")
    restore_files: bool = Field(default=True, alias="restoreFiles")


class RewindToResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message_content: str = Field(alias="messageContent")
    restore_errors: list[str] = Field(alias="restoreErrors")
    restored_paths: list[str] = Field(alias="restoredPaths")


class AuthStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    authenticated: bool
    auth_state: AuthStateKind = Field(alias="authState")
    sign_out_available: bool = Field(alias="signOutAvailable")


def _auth_status_response_from_auth_state(auth_state: AuthState) -> AuthStatusResponse:
    return AuthStatusResponse(
        authenticated=auth_state.can_use_active_provider,
        authState=auth_state.kind,
        signOutAvailable=auth_state.sign_out_available,
    )


@lru_cache(maxsize=1)
def _get_vibe_config_json_schema() -> dict[str, Any]:
    return VibeConfigSchema.model_json_schema(mode="serialization", by_alias=True)


def _dispatch_at_mention_inserted(
    client: TelemetryClient, properties: dict[str, Any]
) -> None:
    client.send_at_mention_inserted(
        nb_mentions=properties.get("nb_mentions", 0),
        context_types=properties.get("context_types", {}),
        file_extensions=properties.get("file_extensions"),
        message_id=properties.get("message_id"),
    )


def _dispatch_user_rating_feedback(
    client: TelemetryClient, properties: dict[str, Any]
) -> None:
    client.send_user_rating_feedback(
        rating=properties.get("rating", 0), model=properties.get("model", "")
    )


_EVENT_DISPATCHERS: dict[str, Callable[[TelemetryClient, dict[str, Any]], None]] = {
    "vibe.at_mention_inserted": _dispatch_at_mention_inserted,
    "vibe.user_rating_feedback": _dispatch_user_rating_feedback,
}


def _internal_error(
    exc: BaseException, detail: str, *, acp_method: str
) -> InternalError:
    capture_sentry_exception(
        exc,
        fatal=False,
        tags={"vibe_boundary": "acp_request_handler", "acp_method": acp_method},
    )
    return InternalError(detail)


@dataclass(frozen=True)
class PendingBrowserSignInAttempt:
    attempt: BrowserSignInAttempt
    provider: ProviderConfig


RETRYABLE_BROWSER_SIGN_IN_COMPLETION_ERRORS = {
    BrowserSignInErrorCode.EXCHANGE_FAILED,
    BrowserSignInErrorCode.POLL_FAILED,
}


OnboardingContextLoader = Callable[[], OnboardingContext]
ApiKeyPersister = Callable[[ProviderConfig, str], str]
ApiKeyRemover = Callable[[ProviderConfig], None]


class BrowserSignInServiceAdapter(Protocol):
    async def authenticate(self) -> str: ...

    async def start_attempt(self) -> BrowserSignInAttempt: ...

    async def complete_attempt(self, attempt: BrowserSignInAttempt) -> str: ...

    async def aclose(self) -> None: ...


BrowserSignInServiceFactory = Callable[[ProviderConfig], BrowserSignInServiceAdapter]


class VibeAcpAgentLoop(AcpAgent):
    client: Client

    def __init__(
        self,
        *,
        onboarding_context_loader: OnboardingContextLoader | None = None,
        browser_sign_in_service_factory: BrowserSignInServiceFactory | None = None,
        api_key_persister: ApiKeyPersister = persist_api_key,
        api_key_remover: ApiKeyRemover = remove_api_key,
        environ_before_dotenv_load: Mapping[str, str] | None = None,
    ) -> None:
        self.sessions: dict[str, AcpSessionLoop] = {}
        self.client_capabilities: ClientCapabilities | None = None
        self.client_info: Implementation | None = None
        self._environ_before_dotenv_load = dict(
            environ_before_dotenv_load
            if environ_before_dotenv_load is not None
            else os.environ
        )
        self._pending_browser_sign_in_attempts: dict[
            str, PendingBrowserSignInAttempt
        ] = {}
        self._load_onboarding_context = (
            onboarding_context_loader or OnboardingContext.load
        )
        self._browser_sign_in_service_factory = (
            browser_sign_in_service_factory or self._build_browser_sign_in_service
        )
        self._persist_api_key = api_key_persister
        self._remove_api_key = api_key_remover

    def _build_browser_auth_method(
        self, context: OnboardingContext, method_id: str
    ) -> AuthMethodAgent | None:
        if not context.supports_browser_sign_in:
            return None

        return AuthMethodAgent(
            id=method_id,
            name="Sign in through Mistral AI Studio",
            description="Sign into Mistral Vibe through your Mistral AI Studio account.",
        )

    def _build_terminal_auth_method(
        self, command: str, args: list[str]
    ) -> TerminalAuthMethod:
        return TerminalAuthMethod(
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

    def _build_browser_sign_in_service(
        self, provider: ProviderConfig | None = None
    ) -> BrowserSignInService:
        provider = provider or self._load_onboarding_context().provider
        if not provider.supports_browser_sign_in:
            raise InvalidRequestError(
                "Browser sign-in is not available for the configured provider."
            )

        browser_base_url = provider.browser_auth_base_url
        api_base_url = provider.browser_auth_api_base_url
        if browser_base_url is None or api_base_url is None:
            raise ConfigurationError("Browser sign-in requires both browser auth URLs.")

        return BrowserSignInService(
            HttpBrowserSignInGateway(
                browser_base_url=browser_base_url, api_base_url=api_base_url
            )
        )

    def _load_enabled_browser_sign_in_context(self) -> OnboardingContext:
        context = self._load_onboarding_context()
        if not context.supports_browser_sign_in:
            raise InvalidRequestError(
                "Browser sign-in is not available for the configured provider."
            )
        return context

    def _supports_delegated_browser_auth(self) -> bool:
        return bool(
            self.client_capabilities
            and self.client_capabilities.field_meta
            and self.client_capabilities.field_meta.get("browser-auth-delegated")
            is True
        )

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

        context = self._load_onboarding_context()

        auth_methods: list[EnvVarAuthMethod | TerminalAuthMethod | AuthMethodAgent] = []
        if browser_auth_method := self._build_browser_auth_method(
            context, "browser-auth"
        ):
            auth_methods.append(browser_auth_method)
        if self._supports_delegated_browser_auth():
            delegated_browser_auth_method = self._build_browser_auth_method(
                context, "browser-auth-delegated"
            )
            if delegated_browser_auth_method is not None:
                auth_methods.append(delegated_browser_auth_method)
        if supports_terminal_auth:
            auth_methods.append(self._build_terminal_auth_method(command, args))

        # JetBrains preemptively shows the auth UI as soon as `authMethods` is
        # non-empty; suppress methods for already-authenticated JetBrains clients.
        _, auth_state = self._assess_current_auth_state()
        if is_jetbrains_client(self.client_info) and auth_state.can_use_active_provider:
            auth_methods = []

        response = InitializeResponse(
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
            auth_methods=auth_methods,
        )
        return response

    async def _authenticate_browser_auth(self, **kwargs: Any) -> AuthenticateResponse:
        action = kwargs.get("action")
        if action not in {None, "start"}:
            raise InvalidRequestError(f"Unsupported browser auth action: {action}")

        provider = self._load_enabled_browser_sign_in_context().provider
        browser_sign_in = self._browser_sign_in_service_factory(provider)
        try:
            api_key = await browser_sign_in.authenticate()
        except BrowserSignInError as e:
            raise _internal_error(e, str(e), acp_method="authenticate") from e
        finally:
            await browser_sign_in.aclose()

        persist_result = self._persist_api_key(
            resolve_api_key_provider(provider), api_key
        )
        return AuthenticateResponse(
            field_meta={
                "browser-auth": {"persistResult": persist_result, "status": "completed"}
            }
        )

    async def _start_delegated_browser_auth(self) -> AuthenticateResponse:
        provider = self._load_enabled_browser_sign_in_context().provider
        browser_sign_in = self._browser_sign_in_service_factory(provider)
        try:
            attempt = await browser_sign_in.start_attempt()
        except BrowserSignInError as e:
            raise _internal_error(e, str(e), acp_method="authenticate") from e
        finally:
            await browser_sign_in.aclose()

        self._pending_browser_sign_in_attempts[attempt.process_id] = (
            PendingBrowserSignInAttempt(attempt=attempt, provider=provider)
        )
        expires_at = attempt.expires_at.astimezone(UTC)
        return AuthenticateResponse(
            field_meta={
                "browser-auth-delegated": {
                    "attemptId": attempt.process_id,
                    "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
                    "signInUrl": attempt.sign_in_url,
                }
            }
        )

    async def _complete_delegated_browser_auth(
        self, **kwargs: Any
    ) -> AuthenticateResponse:
        attempt_id = kwargs.get("attemptId") or kwargs.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise InvalidRequestError("Missing browser sign-in attempt ID.")

        pending_attempt = self._pending_browser_sign_in_attempts.get(attempt_id)
        if pending_attempt is None:
            raise InvalidRequestError(f"Unknown browser sign-in attempt: {attempt_id}")

        browser_sign_in = self._browser_sign_in_service_factory(
            pending_attempt.provider
        )
        try:
            api_key = await browser_sign_in.complete_attempt(pending_attempt.attempt)
        except BrowserSignInError as e:
            if e.code not in RETRYABLE_BROWSER_SIGN_IN_COMPLETION_ERRORS:
                self._pending_browser_sign_in_attempts.pop(attempt_id, None)
            raise InvalidRequestError(str(e)) from e
        finally:
            await browser_sign_in.aclose()

        self._pending_browser_sign_in_attempts.pop(attempt_id, None)
        persist_result = self._persist_api_key(
            resolve_api_key_provider(pending_attempt.provider), api_key
        )
        return AuthenticateResponse(
            field_meta={
                "browser-auth-delegated": {
                    "attemptId": attempt_id,
                    "persistResult": persist_result,
                    "status": "completed",
                }
            }
        )

    async def _authenticate_delegated_browser_auth(
        self, **kwargs: Any
    ) -> AuthenticateResponse:
        action = kwargs.get("action", "start")
        if action not in {"start", "complete"}:
            raise InvalidRequestError(
                f"Unsupported delegated browser auth action: {action}"
            )

        if action == "start":
            return await self._start_delegated_browser_auth()

        return await self._complete_delegated_browser_auth(**kwargs)

    @override
    async def authenticate(
        self, method_id: str, **kwargs: Any
    ) -> AuthenticateResponse | None:
        if method_id == "browser-auth":
            return await self._authenticate_browser_auth(**kwargs)

        if method_id == "browser-auth-delegated":
            return await self._authenticate_delegated_browser_auth(**kwargs)

        raise InvalidRequestError(f"Unsupported auth method: {method_id}")

    def _build_launch_context(self) -> LaunchContext:
        return build_launch_context(
            agent_entrypoint="acp",
            agent_version=__version__,
            client_name=self.client_info.name if self.client_info else "",
            client_version=self.client_info.version if self.client_info else "",
        )

    def _load_config(self) -> VibeConfig:
        try:
            config = VibeConfig.load()
            _merge_non_interactive_disabled_tools(config)
            config.tool_paths.extend(self._get_acp_tool_overrides())
            return config
        except MissingAPIKeyError as e:
            raise UnauthenticatedError.from_missing_api_key(e) from e
        except Exception as e:
            raise ConfigurationError(str(e)) from e

    async def _create_acp_session(
        self, session_id: str, agent_loop: AgentLoop
    ) -> AcpSessionLoop:
        command_registry = AcpCommandRegistry(
            vibe_code_enabled=agent_loop.base_config.vibe_code_enabled
        )
        session = AcpSessionLoop(
            id=session_id, agent_loop=agent_loop, command_registry=command_registry
        )
        self.sessions[session.id] = session

        async def _on_commands_changed() -> None:
            session.spawn(self._send_available_commands(session))

        command_registry.set_on_changed(_on_commands_changed)

        if not agent_loop.bypass_tool_permissions:
            agent_loop.set_approval_callback(self._create_approval_callback(session.id))

        session.spawn(self._send_initial_available_commands(session))
        session.spawn(self._warm_up_agent_loop(session))

        return session

    async def _send_initial_available_commands(self, session: AcpSessionLoop) -> None:
        # Zed can drop session/update notifications sent before it registers
        # the session returned by session/new, so delay initial command discovery.
        await asyncio.sleep(INITIAL_AVAILABLE_COMMANDS_DELAY_SECONDS)
        await self._send_available_commands(session)

    async def _warm_up_agent_loop(self, session: AcpSessionLoop) -> None:
        """Proactively await deferred init so `vibe.ready` telemetry is emitted
        without waiting for the user's first prompt. Errors are swallowed here
        and will resurface on the first `act()` call via `requires_init`.
        """
        try:
            await session.agent_loop.wait_until_ready()
            await self._notify_mcp_discovery_failures(session)
            await self._notify_mcp_auth_required(session)
        except Exception:
            pass

    async def _notify_mcp_discovery_failures(self, session: AcpSessionLoop) -> None:
        """Surface MCP servers that failed to connect during discovery."""
        errors = session.agent_loop.tool_manager.pop_mcp_errors()
        if not errors:
            return
        lines = [
            "The following MCP servers failed to connect:",
            "",
            *(f"- {name}: {err}" for name, err in sorted(errors.items())),
        ]
        await self.client.session_update(
            session_id=session.id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text="\n".join(lines)),
                message_id=str(uuid4()),
            ),
        )

    async def _notify_mcp_auth_required(self, session: AcpSessionLoop) -> None:
        """Show a notice if any enabled MCP servers require OAuth authentication."""
        registry = session.agent_loop.mcp_registry
        if registry is None:
            return
        statuses = registry.status()
        disabled = registry.disabled_aliases()
        aliases = sorted(
            alias
            for alias, status in statuses.items()
            if status is AuthStatus.NEEDS_AUTH and alias not in disabled
        )
        if not aliases:
            return
        message = _mcp_tui_login_message(aliases[0])
        if len(aliases) > 1:
            message = (
                "MCP OAuth login must be performed via the Vibe TUI for these "
                f"servers first: {', '.join(aliases)}.\n\n{message}"
            )
        await self.client.session_update(
            session_id=session.id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=message),
                message_id=str(uuid4()),
            ),
        )

    def _create_agent_loop(
        self, config: VibeConfig, agent_name: str, hook_config_result: Any = None
    ) -> AgentLoop:
        agent_loop = AgentLoop(
            config_orchestrator=LegacyConfigOrchestrator(config),
            agent_name=agent_name,
            enable_streaming=True,
            launch_context=self._build_launch_context(),
            defer_heavy_init=True,
            hook_config_result=hook_config_result,
            cache_store=FileSystemVibeCodeCacheStore(),
        )
        agent_loop.agent_manager.register_agent(CHAT_AGENT)
        return agent_loop

    def _build_session_state(self, session: AcpSessionLoop) -> tuple[Any, Any]:
        modes_state, modes_config = build_mode_state(
            list(session.agent_loop.agent_manager.available_agents.values()),
            session.agent_loop.agent_profile.name,
        )
        return modes_state, modes_config

    def _workspace_trust_details(
        self, cwd: Path
    ) -> tuple[WorkspaceTrustStatus, WorkspaceTrustDetails | None]:
        status = self._workspace_trust_status(cwd)
        if status is not WorkspaceTrustStatus.UNTRUSTED:
            return status, None

        prompt = maybe_build_workspace_trust_prompt(
            cwd, include_explicitly_untrusted=True
        )
        if prompt is None:
            return status, None

        return status, self._build_workspace_trust_details(prompt)

    def _build_workspace_trust_details(
        self, prompt: WorkspaceTrustPrompt
    ) -> WorkspaceTrustDetails:
        return WorkspaceTrustDetails(
            cwd=str(prompt.cwd.resolve()),
            repoRoot=str(prompt.repo_root.resolve()) if prompt.repo_root else None,
            ignoredFiles=self._workspace_trust_ignored_files(prompt),
            availableDecisions=available_workspace_trust_decisions(
                prompt, include_session=False
            ),
        )

    def _workspace_trust_ignored_files(self, prompt: WorkspaceTrustPrompt) -> list[str]:
        found = set(prompt.repo_detected_files)
        if prompt.repo_root is None:
            found.update(prompt.detected_files)
            return sorted(found)

        try:
            cwd_relative = prompt.cwd.resolve().relative_to(prompt.repo_root.resolve())
        except ValueError:
            found.update(prompt.detected_files)
            return sorted(found)

        cwd_prefix = "" if cwd_relative == Path(".") else cwd_relative.as_posix()
        for file in prompt.detected_files:
            found.add(file if not cwd_prefix else f"{cwd_prefix}/{file}")
        return sorted(found)

    def _workspace_trust_status(self, cwd: Path) -> WorkspaceTrustStatus:
        return trusted_folders_manager.trust_status(cwd)

    def _workspace_trust_response(self, cwd: Path) -> WorkspaceTrustStatusResponse:
        status, details = self._workspace_trust_details(cwd)
        return WorkspaceTrustStatusResponse(trust_status=status, details=details)

    def _workspace_trust_meta(self, cwd: Path) -> dict[str, Any]:
        response = self._workspace_trust_response(cwd).model_dump(
            mode="json", by_alias=True
        )
        return {
            WORKSPACE_TRUST_META_KEY: {
                "status": response["trust_status"],
                "details": response["details"],
            }
        }

    def _workspace_trust_prompt_for_decision(
        self, cwd: Path, decision: WorkspaceTrustDecision
    ) -> WorkspaceTrustPrompt:
        prompt = maybe_build_workspace_trust_prompt(
            cwd, include_explicitly_untrusted=True
        )
        if prompt is None:
            raise InvalidRequestError("No workspace trust decision is available.")

        available_decisions = available_workspace_trust_decisions(
            prompt, include_session=False
        )
        if decision not in available_decisions:
            raise InvalidRequestError(f"Unsupported trust decision: {decision}")

        return prompt

    async def _handle_workspace_trust_status(self, params: dict) -> dict[str, Any]:
        try:
            request = WorkspaceTrustStatusRequest.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP workspace trust status request: {exc}"
            ) from exc

        cwd = Path(request.cwd).expanduser().resolve() if request.cwd else Path.cwd()
        return self._workspace_trust_response(cwd).model_dump(
            mode="json", by_alias=True
        )

    async def _handle_workspace_trust_decision(self, params: dict) -> dict[str, Any]:
        try:
            request = WorkspaceTrustDecisionRequest.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP workspace trust decision request: {exc}"
            ) from exc

        cwd = Path(request.cwd).expanduser().resolve() if request.cwd else Path.cwd()
        session = self.sessions.get(request.session_id) if request.session_id else None
        if request.session_id is not None and session is None:
            raise SessionNotFoundError(request.session_id)

        prompt = self._workspace_trust_prompt_for_decision(cwd, request.decision)

        try:
            apply_workspace_trust_decision(prompt, request.decision)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc

        if session is not None and request.decision in TRUST_GRANT_DECISIONS:
            session.spawn(self._reload_trusted_workspace_session(session, cwd))

        return self._workspace_trust_response(cwd).model_dump(
            mode="json", by_alias=True
        )

    @override
    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio | AcpMcpServer]
        | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        load_dotenv_values()
        os.chdir(cwd)

        config = self._load_config()
        hook_config_result = load_hooks_from_fs()

        try:
            agent_loop = self._create_agent_loop(
                config, config.default_agent, hook_config_result=hook_config_result
            )
            # NOTE: For now, we pin session.id to agent_loop.session_id right after init time.
            # We should just use agent_loop.session_id everywhere, but it can still change during
            # session lifetime (e.g. agent_loop.compact is called).
            # We should refactor agent_loop.session_id to make it immutable in ACP context.
            session = await self._create_acp_session(agent_loop.session_id, agent_loop)
        except Exception as e:
            raise ConfigurationError(str(e)) from e

        agent_loop.start_initialize_experiments()

        modes_state, _ = self._build_session_state(session)

        return NewSessionResponse(
            session_id=session.id,
            modes=modes_state,
            config_options=self._build_config_options(session),
            field_meta=self._workspace_trust_meta(Path.cwd()),
        )

    def _get_acp_tool_overrides(self) -> list[Path]:
        overrides = ["todo", "grep", "web_fetch", "web_search", "skill", "task"]

        if self.client_capabilities:
            if self.client_capabilities.terminal:
                overrides.append("bash")
            if self.client_capabilities.fs:
                fs = self.client_capabilities.fs
                if fs.read_text_file:
                    overrides.append("read_file")
                if fs.write_text_file:
                    overrides.extend(["write_file", "edit"])

        return [
            VIBE_ROOT / "acp" / "tools" / "builtins" / f"{override}.py"
            for override in overrides
        ]

    def _create_approval_callback(self, session_id: str) -> ApprovalCallback:
        session = self._get_session(session_id)

        async def _handle_permission_selection(
            option_id: str,
            tool_name: str,
            required_permissions: list[RequiredPermission] | None,
        ) -> tuple[ApprovalResponse, str | None]:
            match option_id:
                case ToolOption.ALLOW_ONCE:
                    return (ApprovalResponse.YES, None)
                case ToolOption.ALLOW_ALWAYS:
                    await session.agent_loop.approve_always(
                        tool_name, required_permissions
                    )
                    return (ApprovalResponse.YES, None)
                case ToolOption.ALLOW_ALWAYS_PERMANENT:
                    await session.agent_loop.approve_always(
                        tool_name, required_permissions, save_permanently=True
                    )
                    return (ApprovalResponse.YES, None)
                case ToolOption.REJECT_ONCE:
                    session.agent_loop.telemetry_client.send_user_cancelled_action(
                        "reject_approval"
                    )
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
                return await _handle_permission_selection(
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

    def _find_acp_session_by_vibe_session_id(
        self, session_id: str
    ) -> AcpSessionLoop | None:
        for candidate in self.sessions.values():
            if candidate.agent_loop.session_id == session_id:
                return candidate

        return None

    def _find_live_session_by_requested_session_id(
        self, session_id: str
    ) -> AcpSessionLoop | None:
        return self.sessions.get(
            session_id
        ) or self._find_acp_session_by_vibe_session_id(session_id)

    def _load_session_logging_config(self) -> SessionLoggingConfig:
        try:
            return VibeConfig.load().session_logging
        except MissingAPIKeyError:
            try:
                persisted_config = VibeConfig.get_persisted_config()
                return SessionLoggingConfig.model_validate(
                    persisted_config.get("session_logging", {})
                )
            except Exception as e:
                raise ConfigurationError(str(e)) from e
        except Exception as e:
            raise ConfigurationError(str(e)) from e

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

    async def _replay_tool_calls(self, session_id: str, msg: LLMMessage) -> set[str]:
        if not msg.tool_calls:
            return set()

        agent_loop = self._get_session(session_id).agent_loop
        parsed = agent_loop.format_handler.parse_message(msg)
        resolved = agent_loop.format_handler.resolve_tool_calls(
            parsed, agent_loop.tool_manager
        )
        resolved_by_id = {call.call_id: call for call in resolved.tool_calls}

        emitted_call_ids: set[str] = set()
        for tool_call in msg.tool_calls:
            if not (tool_call.id and tool_call.function.name):
                continue
            update = tool_call_replay_update(
                resolved_by_id.get(tool_call.id), tool_call
            )
            if update:
                emitted_call_ids.add(tool_call.id)
                await self.client.session_update(session_id=session_id, update=update)
        return emitted_call_ids

    async def _replay_conversation_history(
        self, session_id: str, messages: list[LLMMessage]
    ) -> None:
        replayed_call_ids: set[str] = set()
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
                replayed_call_ids |= await self._replay_tool_calls(session_id, msg)

            elif msg.role == Role.tool:
                # Skip results whose call was not replayed (e.g. hidden tools
                # like todo): emitting one would orphan a tool_call_update with
                # no preceding tool_call.
                if msg.tool_call_id not in replayed_call_ids:
                    continue
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

        commands.sort(key=lambda command: command.name)
        await self.client.session_update(
            session_id=session.id, update=update_available_commands(commands)
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
        load_dotenv_values()
        os.chdir(cwd)

        config = self._load_config()
        hook_config_result = load_hooks_from_fs()

        session_dir = SessionLoader.find_session_by_id(
            session_id, config.session_logging
        )
        if session_dir is None:
            raise SessionNotFoundError(session_id)

        try:
            loaded_messages, metadata = SessionLoader.load_session(session_dir)
        except Exception as e:
            raise SessionLoadError(session_id, str(e)) from e

        try:
            agent_loop = self._create_agent_loop(
                config, config.default_agent, hook_config_result=hook_config_result
            )
        except Exception as e:
            raise ConfigurationError(str(e)) from e
        loaded_session_id = metadata.get("session_id", agent_loop.session_id)
        agent_loop.session_id = loaded_session_id
        agent_loop.parent_session_id = metadata.get("parent_session_id")
        agent_loop.session_logger.resume_existing_session(
            loaded_session_id, session_dir
        )
        await agent_loop.hydrate_experiments_from_session()

        non_system_messages = [
            msg for msg in loaded_messages if msg.role != Role.system
        ]
        if non_system_messages:
            agent_loop.messages.extend(non_system_messages)
        session = await self._create_acp_session(session_id, agent_loop)
        await self._replay_conversation_history(session.id, non_system_messages)
        self._send_usage_update(session)

        modes_state, _ = self._build_session_state(session)

        return LoadSessionResponse(
            modes=modes_state,
            config_options=self._build_config_options(session),
            field_meta=self._workspace_trust_meta(Path.cwd()),
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
        await self._reload_session_config(session)
        if command_registry := getattr(session, "command_registry", None):
            await command_registry.notify_changed()

    async def _apply_model_change(self, session: AcpSessionLoop, model_id: str) -> bool:
        model_aliases = list(session.agent_loop.config.models)
        if model_id not in model_aliases:
            return False

        VibeConfig.save_updates({"active_model": model_id})
        await self._reload_config(session)
        return True

    async def _apply_thinking_change(
        self, session: AcpSessionLoop, level: ThinkingLevel
    ) -> bool:
        active_model = session.agent_loop.config.get_active_model()
        await session.agent_loop.config_orchestrator.set_field(
            f"/models/{escape_json_pointer_token(active_model.alias)}/thinking", level
        )
        await self._reload_config(session)
        return True

    @override
    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        session = self._get_session(session_id)

        if not await self._apply_mode_change(session, mode_id):
            return None

        return SetSessionModeResponse()

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
            case "max_turns" if isinstance(value, str):
                try:
                    max_turns = int(value)
                except ValueError:
                    success = False
                else:
                    session.agent_loop.set_max_turns(max_turns)
                    success = True
            case "max_tokens" if isinstance(value, str):
                try:
                    max_tokens = int(value)
                except ValueError:
                    success = False
                else:
                    session.agent_loop.set_max_tokens(max_tokens)
                    success = True
            case _:
                success = False

        if not success:
            return None

        return SetSessionConfigOptionResponse(
            config_options=self._build_config_options(session)
        )

    @override
    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
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
        self, session_id: str, prompt: list[ContentBlock], **kwargs: Any
    ) -> PromptResponse:
        session = self._get_session(session_id)

        if session.prompt_task is not None:
            raise InvalidRequestError(
                "Concurrent prompts are not supported yet, wait for agent loop to finish"
            )

        try:
            user_display_content = parse_user_display_content_metadata(
                kwargs.get(USER_DISPLAY_CONTENT_META_KEY)
            )
        except ValidationError as e:
            raise InvalidRequestError(
                f"Invalid user display content metadata: {e}"
            ) from e

        text_prompt = self._build_text_prompt(prompt)
        resolved_message_id = await self._prepare_prompt_message_id(
            session, text_prompt, user_display_content
        )

        if command_response := await self._maybe_handle_builtin_command(
            session, text_prompt, resolved_message_id
        ):
            return command_response

        try:
            skill = session.agent_loop.skill_manager.parse_skill_command(text_prompt)
        except OSError as e:
            raise _internal_error(
                e, f"Failed to read skill file: {e}", acp_method="session/prompt"
            ) from e

        if skill:
            session.agent_loop.telemetry_client.send_slash_command_used(
                skill.name, "skill"
            )

        auto_title: str | None = None
        if session.agent_loop.session_logger.needs_initial_auto_title():
            auto_title = (
                format_session_title(acp_blocks_to_title_segments(prompt)) or None
            )

        images = extract_image_attachments(
            prompt, session_dir=session.agent_loop.session_logger.session_dir
        )

        async def agent_loop_task() -> None:
            async for update in self._run_agent_loop(
                session,
                text_prompt,
                resolved_message_id,
                auto_title=auto_title,
                user_display_content=user_display_content,
                images=images,
            ):
                await self.client.session_update(session_id=session.id, update=update)

        async with session.mutating("start a new prompt"):
            task = session.set_prompt_task(agent_loop_task())

        try:
            await task

        except asyncio.CancelledError:
            self._send_usage_update(session)
            return PromptResponse(
                stop_reason="cancelled", usage=self._build_usage(session)
            )

        except CoreRateLimitError as e:
            raise RateLimitError.from_core(e) from e

        except CoreContextTooLongError as e:
            raise ContextTooLongError.from_core(e) from e

        except CoreResponseTooLongError:
            self._send_usage_update(session)
            return PromptResponse(
                stop_reason="max_tokens", usage=self._build_usage(session)
            )

        except CoreRefusalError as e:
            raise RefusalError.from_core(e) from e

        except CompactionFailedError as e:
            raise CompactionError.from_core(e) from e

        except ConversationLimitException as e:
            raise ConversationLimitError(str(e)) from e

        except ImagesNotSupportedError as e:
            raise AcpImagesNotSupportedError(str(e)) from e

        except Exception as e:
            raise _internal_error(e, str(e), acp_method="session/prompt") from e

        self._send_usage_update(session)
        meta = self._build_end_turn_meta(session)
        return PromptResponse(
            stop_reason="end_turn",
            usage=self._build_usage(session),
            field_meta=meta or None,
        )

    def _build_end_turn_meta(self, session: AcpSessionLoop) -> dict[str, Any] | None:
        agent_loop = session.agent_loop
        user_message_count = (
            sum(m.role == Role.user and not m.injected for m in agent_loop.messages) + 1
        )  # +1 for the message just sent
        if not should_show_feedback(
            telemetry_active=agent_loop.telemetry_client.is_active(),
            is_mistral_model=agent_loop.config.is_active_model_mistral(),
            user_message_count=user_message_count,
            cache_store=agent_loop.cache_store,
        ):
            return None
        record_feedback_asked(agent_loop.cache_store)
        return {"show_feedback_prompt": True}

    def _build_text_prompt(self, acp_prompt: list[ContentBlock]) -> str:
        def _is_automatic_resource(block: ContentBlock) -> bool:
            return block.type == "resource" and bool(
                block.field_meta and block.field_meta.get("automatic")
            )

        ordered = [b for b in acp_prompt if not _is_automatic_resource(b)] + [
            b for b in acp_prompt if _is_automatic_resource(b)
        ]

        text_prompt = ""
        for block in ordered:
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
                    # making it more detailed than the output of the read tool.
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
                case "image":
                    # Images carry no prompt text; they are extracted separately
                    # via extract_image_attachments and passed to act(images=...).
                    continue
                case _:
                    raise InvalidRequestError(
                        f"We currently don't support {block.type} content blocks"
                    )
        return text_prompt

    def _get_builtin_command(
        self, session: AcpSessionLoop, text_prompt: str
    ) -> tuple[str, AcpCommand] | None:
        normalized = text_prompt.strip().lower()
        parts = normalized.split(None, 1)
        if not parts or not parts[0].startswith("/"):
            return None

        cmd_name = parts[0][1:]  # strip leading "/"
        command = session.command_registry.get(cmd_name)
        if command is None:
            return None

        return cmd_name, command

    def _is_builtin_command(self, session: AcpSessionLoop, text_prompt: str) -> bool:
        return self._get_builtin_command(session, text_prompt) is not None

    async def _prepare_prompt_message_id(
        self,
        session: AcpSessionLoop,
        text_prompt: str,
        user_display_content: UserDisplayContentMetadata | None,
    ) -> str:
        message_id = str(uuid4())
        if self._is_builtin_command(session, text_prompt):
            await self._emit_user_message_chunk(
                session.id, text_prompt, message_id, user_display_content
            )
        return message_id

    async def _maybe_handle_builtin_command(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse | None:
        command_info = self._get_builtin_command(session, text_prompt)
        if command_info is None:
            return None

        cmd_name, command = command_info

        session.agent_loop.telemetry_client.send_slash_command_used(cmd_name, "builtin")
        handler = getattr(self, command.handler)
        return await handler(session, text_prompt, message_id)

    async def _emit_user_message_chunk(
        self,
        session_id: str,
        content: str,
        message_id: str,
        user_display_content: UserDisplayContentMetadata | None = None,
    ) -> None:
        field_meta = self._user_display_content_meta(user_display_content)
        await self.client.session_update(
            session_id=session_id,
            update=UserMessageChunk(
                session_update="user_message_chunk",
                content=TextContentBlock(type="text", text=content),
                message_id=message_id,
                field_meta=field_meta,
            ),
        )

    @staticmethod
    def _user_display_content_meta(
        user_display_content: UserDisplayContentMetadata | None,
    ) -> dict[str, Any] | None:
        if user_display_content is None:
            return None
        return {
            USER_DISPLAY_CONTENT_META_KEY: user_display_content.model_dump(mode="json")
        }

    async def _run_agent_loop(
        self,
        session: AcpSessionLoop,
        prompt: str,
        client_message_id: str | None = None,
        *,
        auto_title: str | None = None,
        user_display_content: UserDisplayContentMetadata | None = None,
        images: list[ImageAttachment] | None = None,
    ) -> AsyncGenerator[SessionUpdate | UsageUpdate]:
        async with aclosing(
            session.agent_loop.act(
                prompt,
                client_message_id=client_message_id,
                auto_title=auto_title,
                user_display_content=user_display_content,
                images=images or None,
            )
        ) as events:
            async for event in events:
                if isinstance(event, UserMessageEvent):
                    field_meta = self._user_display_content_meta(user_display_content)
                    yield UserMessageChunk(
                        session_update="user_message_chunk",
                        content=TextContentBlock(type="text", text=event.content),
                        message_id=event.message_id,
                        field_meta=field_meta,
                    )

                elif isinstance(event, SessionTitleUpdatedEvent):
                    await self._emit_session_info_update(
                        session.id, title=event.title, updated_at=None
                    )

                elif isinstance(event, AssistantEvent):
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
                        )

                    # A starting tool call is pending until it streams or
                    # finishes; the host drops created events without a status.
                    session_update = tool_call_session_update(event, status="pending")
                    if session_update:
                        yield session_update

                elif isinstance(event, ToolResultEvent):
                    session_update = tool_result_session_update(event)
                    if session_update:
                        yield session_update
                    self._send_usage_update(session)

                elif isinstance(event, ToolTerminalOpenedEvent):
                    # bash yielded the terminal id; surface it as an
                    # in_progress update carrying the terminal block.
                    yield ToolCallProgress(
                        session_update="tool_call_update",
                        tool_call_id=event.tool_call_id,
                        status="in_progress",
                        kind=resolve_kind(event.tool_name),
                        content=[
                            TerminalToolCallContent(
                                type="terminal", terminal_id=event.terminal_id
                            )
                        ],
                        field_meta={"tool_name": event.tool_name},
                    )

                elif isinstance(event, ToolStreamEvent):
                    yield ToolCallProgress(
                        session_update="tool_call_update",
                        tool_call_id=event.tool_call_id,
                        kind=resolve_kind(event.tool_name),
                        content=[
                            ContentToolCallContent(
                                type="content",
                                content=TextContentBlock(
                                    type="text", text=event.message
                                ),
                            )
                        ],
                        field_meta={"tool_name": event.tool_name},
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

        session.agent_loop.emit_session_closed_telemetry()
        await session.close()
        await self._close_agent_loop(session.agent_loop)

        return CloseSessionResponse()

    async def emit_session_closed_for_active_sessions(self) -> None:
        agent_loops = [session.agent_loop for session in self.sessions.values()]
        for agent_loop in agent_loops:
            agent_loop.telemetry_client._client = None
            agent_loop.emit_session_closed_telemetry()
        await asyncio.gather(
            *(agent_loop.telemetry_client.aclose() for agent_loop in agent_loops),
            return_exceptions=True,
        )

    async def _close_agent_loop(self, agent_loop: AgentLoop) -> None:
        deferred_init_thread = agent_loop._deferred_init_thread
        if deferred_init_thread is not None and deferred_init_thread.is_alive():
            await asyncio.to_thread(deferred_init_thread.join)

        await agent_loop.aclose()
        await agent_loop.telemetry_client.aclose()

    @override
    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self._get_session(session_id)
        session.agent_loop.telemetry_client.send_user_cancelled_action(
            "interrupt_agent"
        )
        await session.cancel_prompt()

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
        load_dotenv_values()
        os.chdir(cwd)

        source_session = self._get_session(session_id)
        try:
            message_id = ForkSessionParams.model_validate(kwargs).message_id
        except ValidationError as e:
            raise InvalidRequestError(f"Invalid fork parameters: {e}") from e

        try:
            async with source_session.mutating("fork a session"):
                agent_loop = await source_session.agent_loop.fork(message_id)
                agent_loop.agent_manager.register_agent(CHAT_AGENT)
                session = await self._create_acp_session(
                    agent_loop.session_id, agent_loop
                )
        except InvalidRequestError:
            raise
        except ValueError as e:
            raise InvalidRequestError(str(e)) from e
        except Exception as e:
            raise ConfigurationError(str(e)) from e

        modes_state, _ = self._build_session_state(session)

        return ForkSessionResponse(
            session_id=session.id,
            modes=modes_state,
            config_options=self._build_config_options(session),
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
        raise NotImplementedMethodError("resume_session")

    async def _emit_session_info_update(
        self, session_id: str, *, title: str, updated_at: str | None
    ) -> None:
        update_kwargs: dict[str, Any] = {
            "session_update": "session_info_update",
            "title": title,
        }
        if updated_at is not None:
            update_kwargs["updated_at"] = updated_at

        await self.client.session_update(
            session_id=session_id, update=SessionInfoUpdate(**update_kwargs)
        )

    async def _delete_saved_session(self, session_id: str) -> None:
        try:
            await delete_saved_session(session_id, self._load_session_logging_config())
        except ValueError as exc:
            raise SessionNotFoundError(session_id) from exc

    def _live_session_has_saved_history(self, session: AcpSessionLoop) -> bool:
        logger = session.agent_loop.session_logger
        return (
            logger.enabled
            and logger.session_dir is not None
            and logger.metadata_filepath.exists()
        )

    async def _persist_live_session_title(
        self, session: AcpSessionLoop, title: str
    ) -> dict[str, Any] | None:
        logger = session.agent_loop.session_logger
        if not logger.enabled or logger.session_dir is None:
            return None
        if not logger.metadata_filepath.exists():
            return None

        try:
            return await update_saved_session_title_at_path(logger.session_dir, title)
        except ValueError as exc:
            raise _internal_error(
                exc,
                f"Failed to persist title update for session {logger.session_id}: {exc}",
                acp_method="session/set_title",
            ) from exc

    def _set_live_session_title(self, session: AcpSessionLoop, title: str) -> None:
        try:
            session.agent_loop.session_logger.set_title(title)
        except ValueError as exc:
            raise InvalidRequestError(
                f"Invalid ACP session title request: {exc}"
            ) from exc

    async def _handle_session_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            request = SessionDeleteRequest.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP session delete request: {exc}"
            ) from exc

        live_session = self._find_live_session_by_requested_session_id(
            request.session_id
        )
        if live_session is None:
            await self._delete_saved_session(request.session_id)
            return {}

        saved_session_id = live_session.agent_loop.session_id
        has_saved_history = self._live_session_has_saved_history(live_session)

        await self.close_session(live_session.id)

        if not has_saved_history:
            return {}

        try:
            await delete_saved_session(
                saved_session_id, self._load_session_logging_config()
            )
        except ValueError as exc:
            raise _internal_error(
                exc,
                f"Failed to delete saved session {saved_session_id}: {exc}",
                acp_method="session/delete",
            ) from exc

        return {}

    async def _handle_session_set_title(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            request = SessionSetTitleRequest.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP session title request: {exc}"
            ) from exc

        live_session = self._find_live_session_by_requested_session_id(
            request.session_id
        )
        if live_session is None:
            try:
                metadata = await update_saved_session_title(
                    request.session_id,
                    request.title,
                    self._load_session_logging_config(),
                )
            except ValueError as exc:
                raise SessionNotFoundError(request.session_id) from exc

            await self._emit_session_info_update(
                request.session_id,
                title=request.title,
                updated_at=metadata.get("end_time"),
            )
            return {}

        persisted_metadata = await self._persist_live_session_title(
            live_session, request.title
        )
        self._set_live_session_title(live_session, request.title)
        updated_at = (
            persisted_metadata.get("end_time")
            if persisted_metadata is not None
            else (
                live_session.agent_loop.session_logger.session_metadata.end_time
                if live_session.agent_loop.session_logger.session_metadata is not None
                else None
            )
        )

        await self._emit_session_info_update(
            live_session.id, title=request.title, updated_at=updated_at
        )
        return {}

    def _live_session_for_rewind(self, session_id: str) -> AcpSessionLoop:
        live_session = self._find_live_session_by_requested_session_id(session_id)
        if live_session is None:
            raise SessionNotFoundError(session_id)
        return live_session

    async def _handle_rewind_preview(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            request = RewindPreviewRequest.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(
                f"Invalid ACP rewind preview request: {exc}"
            ) from exc

        session = self._live_session_for_rewind(request.session_id)
        rewind_manager = session.agent_loop.rewind_manager
        try:
            async with session.reading():
                index = rewind_manager.index_for_message_id(request.message_id)
                paths = rewind_manager.restorable_paths_at(index)
        except RewindError as exc:
            raise InvalidRequestError(str(exc)) from exc

        response = RewindPreviewResponse(paths=paths)
        return response.model_dump(mode="json", by_alias=True)

    async def _handle_rewind_to(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            request = RewindToRequest.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequestError(f"Invalid ACP rewind request: {exc}") from exc

        session = self._live_session_for_rewind(request.session_id)
        rewind_manager = session.agent_loop.rewind_manager
        try:
            async with session.mutating("rewind a session"):
                index = rewind_manager.index_for_message_id(request.message_id)
                (
                    content,
                    errors,
                    restored_paths,
                ) = await rewind_manager.rewind_to_message(
                    index, restore_files=request.restore_files, inplace=True
                )
        except RewindError as exc:
            raise InvalidRequestError(str(exc)) from exc

        response = RewindToResponse(
            messageContent=content, restoreErrors=errors, restoredPaths=restored_paths
        )
        return response.model_dump(mode="json", by_alias=True)

    def _assess_current_auth_state(self) -> tuple[ProviderConfig, AuthState]:
        load_dotenv_values(env_path=GLOBAL_ENV_FILE.path)
        provider = self._load_onboarding_context().provider
        auth_state = assess_auth_state(
            provider,
            process_env_had_value_before_dotenv_load=bool(
                provider.api_key_env_var
                and self._environ_before_dotenv_load.get(provider.api_key_env_var)
            ),
        )
        return provider, auth_state

    def _handle_auth_status(self) -> dict[str, Any]:
        _, auth_state = self._assess_current_auth_state()
        return _auth_status_response_from_auth_state(auth_state).model_dump(
            mode="json", by_alias=True
        )

    def _handle_auth_sign_out(self) -> dict[str, Any]:
        provider, auth_state = self._assess_current_auth_state()
        if not auth_state.sign_out_available:
            raise InvalidRequestError(
                f"Sign out is not available for auth state: {auth_state.kind.value}"
            )

        try:
            self._remove_api_key(provider)
        except (OSError, ValueError, KeyringError) as exc:
            raise _internal_error(
                exc, f"Failed to sign out: {exc}", acp_method="auth/signOut"
            ) from exc

        return {}

    def _handle_config_schema(self) -> dict[str, Any]:
        return ConfigSchemaResponse(
            version=__version__, schema=_get_vibe_config_json_schema()
        ).model_dump(mode="json", by_alias=True)

    @override
    async def ext_method(self, method: str, params: dict) -> dict:
        if method == "config/schema":
            return self._handle_config_schema()
        if method == "auth/status":
            return self._handle_auth_status()
        if method == "auth/signOut":
            return self._handle_auth_sign_out()

        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]] = {
            "session/set_title": self._handle_session_set_title,
            "session/delete": self._handle_session_delete,
            "trust/status": self._handle_workspace_trust_status,
            "trust/decision": self._handle_workspace_trust_decision,
            "rewind/preview": self._handle_rewind_preview,
            "rewind/to": self._handle_rewind_to,
        }
        handler = handlers.get(method)
        if handler is None:
            raise NotImplementedMethodError(method)
        return await handler(params)

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

        properties = {
            "model": session.agent_loop.config.active_model,
            **notification.properties,
        }
        dispatcher(session.agent_loop.telemetry_client, properties)

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
        return PromptResponse(stop_reason="end_turn")

    async def _handle_teleport(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        return await handle_teleport_command(self.client, session, message_id)

    async def _handle_help(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        lines = ["### Available Commands", ""]
        for cmd in sorted(
            session.command_registry.commands.values(), key=lambda command: command.name
        ):
            hint = f" `<{cmd.input_hint}>`" if cmd.input_hint else ""
            lines.append(f"- `/{cmd.name}`{hint}: {cmd.description}")

        builtin_names = set(session.command_registry.commands)
        invocable = sorted(
            (
                skill
                for skill in session.agent_loop.skill_manager.available_skills.values()
                if skill.user_invocable and skill.name not in builtin_names
            ),
            key=lambda skill: skill.name,
        )
        if invocable:
            lines.extend(["", "### Available Skills", ""])
            for skill in invocable:
                lines.append(f"- `/{skill.name}`: {skill.description}")

        return await self._command_reply(session, "\n".join(lines), message_id)

    async def _handle_mcp(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        parts = text_prompt.strip().split(None, _MCP_COMMAND_MAX_SPLITS)
        subcommand = parts[1].lower() if len(parts) > 1 else "status"
        arg = (
            parts[_MCP_COMMAND_ARG_INDEX].strip()
            if len(parts) > _MCP_COMMAND_ARG_INDEX
            else ""
        )

        match subcommand:
            case "status":
                return await self._handle_mcp_status(session, arg, message_id)
            case "login":
                return await self._handle_mcp_login(session, arg, message_id)
            case "logout":
                return await self._handle_mcp_logout(session, arg, message_id)
            case _:
                return await self._command_reply(
                    session,
                    "Usage: `/mcp status`, `/mcp login <alias>`, or `/mcp logout <alias>`",
                    message_id,
                )

    async def _handle_mcp_status(
        self, session: AcpSessionLoop, arg: str, message_id: str
    ) -> PromptResponse:
        if arg:
            return await self._command_reply(
                session, "Usage: `/mcp status`", message_id
            )
        await session.agent_loop.wait_until_ready()
        registry = session.agent_loop.mcp_registry
        statuses = registry.status() if registry is not None else {}
        if not statuses:
            return await self._command_reply(
                session, "No MCP servers configured.", message_id
            )
        lines = ["### MCP auth status", ""]
        for alias, status in sorted(statuses.items()):
            lines.append(f"- `{alias}`: `{status.value}`")
        return await self._command_reply(session, "\n".join(lines), message_id)

    async def _handle_mcp_login(
        self, session: AcpSessionLoop, alias: str, message_id: str
    ) -> PromptResponse:
        if not alias:
            return await self._command_reply(
                session, "Usage: `/mcp login <alias>`", message_id
            )
        await session.agent_loop.wait_until_ready()
        registry = session.agent_loop.mcp_registry
        statuses = registry.status() if registry is not None else {}
        if alias not in statuses:
            return await self._command_reply(
                session, f"Unknown MCP server: `{alias}`", message_id
            )
        if statuses[alias] in {AuthStatus.STATIC, AuthStatus.STDIO}:
            return await self._command_reply(
                session,
                f"MCP server `{alias}` is not configured for OAuth.",
                message_id,
            )
        return await self._command_reply(
            session, _mcp_tui_login_message(alias), message_id
        )

    async def _handle_mcp_logout(
        self, session: AcpSessionLoop, alias: str, message_id: str
    ) -> PromptResponse:
        if not alias:
            return await self._command_reply(
                session, "Usage: `/mcp logout <alias>`", message_id
            )
        await session.agent_loop.wait_until_ready()
        registry = session.agent_loop.mcp_registry
        if registry is None:
            return await self._command_reply(
                session, "No MCP servers configured.", message_id
            )
        try:
            await registry.logout(alias)
            await session.agent_loop.tool_manager.refresh_remote_tools_async()
            await session.agent_loop.refresh_system_prompt()
        except (MCPOAuthError, ValueError) as exc:
            return await self._command_reply(session, str(exc), message_id)
        return await self._command_reply(
            session, f"MCP server `{alias}` logged out.", message_id
        )

    async def _handle_compact(
        self, session: AcpSessionLoop, text_prompt: str, message_id: str
    ) -> PromptResponse:
        if len(session.agent_loop.messages) <= 1:
            return await self._command_reply(
                session, "No conversation history to compact yet.", message_id
            )

        tool_call_id = str(uuid4())
        old_tokens = session.agent_loop.stats.context_tokens
        old_session_id = session.agent_loop.session_id
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

        try:
            async with session.mutating("compact a session"):
                await session.agent_loop.compact(extra_instructions=cmd_args.strip())
        except CompactionFailedError as e:
            raise CompactionError.from_core(e) from e

        end_event = CompactEndEvent(
            summary_length=0,
            old_session_id=old_session_id,
            new_session_id=session.agent_loop.session_id,
            tool_call_id=tool_call_id,
        )
        await self.client.session_update(
            session_id=session.id, update=create_compact_end_session_update(end_event)
        )

        return PromptResponse(stop_reason="end_turn")

    async def _reload_session_config(self, session: AcpSessionLoop) -> None:
        """Reload config from disk and reinitialize the agent loop."""
        tool_paths = session.agent_loop.config.tool_paths
        await session.agent_loop.config_orchestrator.reload()
        new_config = session.agent_loop.config_orchestrator.config
        new_config.tool_paths = tool_paths
        _merge_non_interactive_disabled_tools(new_config)
        await session.agent_loop.reload_with_initial_messages()
        if command_registry := getattr(session, "command_registry", None):
            command_registry.refresh(
                AcpCommandContext(vibe_code_enabled=new_config.vibe_code_enabled)
            )

    async def _reload_trusted_workspace_session(
        self, session: AcpSessionLoop, cwd: Path
    ) -> None:
        try:
            os.chdir(cwd)
            await self._reload_session_config(session)
            await session.command_registry.notify_changed()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Failed to reload trusted workspace session config: session_id=%s cwd=%s",
                session.id,
                cwd,
                exc_info=exc,
            )

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
        models_config = build_model_config(
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


SESSION_CLOSED_FLUSH_TIMEOUT_SECONDS = 1.0


def run_acp_server(
    *, environ_before_dotenv_load: Mapping[str, str] | None = None
) -> None:
    agent = VibeAcpAgentLoop(environ_before_dotenv_load=environ_before_dotenv_load)
    install_sigterm_flush = TelemetryClient(config_getter=VibeConfig.load).is_active()
    received_sigterm = False
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _handle_sigterm(_signum: int, _frame: Any) -> None:
        nonlocal received_sigterm
        received_sigterm = True
        raise KeyboardInterrupt

    if install_sigterm_flush:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        asyncio.run(
            run_agent(
                agent=agent,
                use_unstable_protocol=True,
                observers=[acp_message_observer],
            )
        )
    except KeyboardInterrupt:
        if received_sigterm:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
            try:
                asyncio.run(
                    asyncio.wait_for(
                        agent.emit_session_closed_for_active_sessions(),
                        timeout=SESSION_CLOSED_FLUSH_TIMEOUT_SECONDS,
                    )
                )
            except (TimeoutError, Exception):
                pass
        # This is expected when the server is terminated
        pass
    except Exception as e:
        # Log any unexpected errors
        print(f"ACP Agent Server error: {e}", file=sys.stderr)
        raise
    finally:
        if install_sigterm_flush:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)
