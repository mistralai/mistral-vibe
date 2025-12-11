from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
import sys
from typing import Any, cast, override

from acp import (
    PROTOCOL_VERSION,
    Agent as AcpAgent,
    AgentSideConnection,
    AuthenticateRequest,
    CancelNotification,
    InitializeRequest,
    InitializeResponse,
    LoadSessionRequest,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    RequestError,
    RequestPermissionRequest,
    SessionNotification,
    SetSessionModelRequest,
    SetSessionModelResponse,
    SetSessionModeRequest,
    SetSessionModeResponse,
    stdio_streams,
)
from acp.helpers import ContentBlock, SessionUpdate
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    AuthenticateResponse,
    AuthMethod,
    Implementation,
    ModelInfo,
    PromptCapabilities,
    SessionModelState,
    SessionModeState,
    TextContentBlock,
    TextResourceContents,
    ToolCall,
)
from pydantic import BaseModel, ConfigDict

from vibe import VIBE_ROOT
from vibe.acp.tools.base import BaseAcpTool
from vibe.acp.tools.session_update import (
    tool_call_session_update,
    tool_result_session_update,
)
from vibe.acp.utils import TOOL_OPTIONS, ToolOption, VibeSessionMode
from vibe.core import __version__
from vibe.core.agent import Agent as VibeAgent
from vibe.core.autocompletion.path_prompt_adapter import render_path_prompt
from vibe.core.config import MissingAPIKeyError, VibeConfig, load_api_keys_from_env
from vibe.core.types import (
    AssistantEvent,
    AsyncApprovalCallback,
    ToolCallEvent,
    ToolResultEvent,
)
from vibe.core.utils import CancellationReason, get_user_cancellation_message


class AcpSession(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str
    agent: VibeAgent
    mode_id: VibeSessionMode = VibeSessionMode.APPROVAL_REQUIRED
    task: asyncio.Task[None] | None = None


class VibeAcpAgent(AcpAgent):
    def __init__(self, connection: AgentSideConnection) -> None:
        self.sessions: dict[str, AcpSession] = {}
        self.connection = connection
        self.client_capabilities = None

    @override
    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        self.client_capabilities = params.clientCapabilities

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

        auth_methods = (
            [
                AuthMethod(
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
            agentCapabilities=AgentCapabilities(
                loadSession=False,
                promptCapabilities=PromptCapabilities(
                    audio=False, embeddedContext=True, image=False
                ),
            ),
            protocolVersion=PROTOCOL_VERSION,
            agentInfo=Implementation(
                name="@mistralai/mistral-vibe",
                title="Mistral Vibe",
                version=__version__,
            ),
            authMethods=auth_methods,
        )
        return response

    @override
    async def authenticate(
        self, params: AuthenticateRequest
    ) -> AuthenticateResponse | None:
        raise NotImplementedError("Not implemented yet")

    @override
    async def newSession(self, params: NewSessionRequest) -> NewSessionResponse:
        capability_disabled_tools = self._get_disabled_tools_from_capabilities()
        load_api_keys_from_env()
        try:
            config = VibeConfig.load(
                workdir=Path(params.cwd),
                tool_paths=[str(VIBE_ROOT / "acp" / "tools" / "builtins")],
                disabled_tools=capability_disabled_tools,
            )
        except MissingAPIKeyError as e:
            raise RequestError.auth_required({
                "message": "You must be authenticated before creating a new session"
            }) from e

        agent = VibeAgent(config=config, auto_approve=False, enable_streaming=True)
        # NOTE: For now, we pin session.id to agent.session_id right after init time.
        # We should just use agent.session_id everywhere, but it can still change during
        # session lifetime (e.g. agent.compact is called).
        # We should refactor agent.session_id to make it immutable in ACP context.
        session = AcpSession(id=agent.session_id, agent=agent)
        self.sessions[session.id] = session

        if not agent.auto_approve:
            agent.set_approval_callback(
                self._create_approval_callback(agent.session_id)
            )

        response = NewSessionResponse(
            sessionId=agent.session_id,
            models=SessionModelState(
                currentModelId=agent.config.active_model,
                availableModels=[
                    ModelInfo(modelId=model.alias, name=model.alias)
                    for model in agent.config.models
                ],
            ),
            modes=SessionModeState(
                currentModeId=session.mode_id,
                availableModes=VibeSessionMode.get_all_acp_session_modes(),
            ),
        )
        return response

    def _get_disabled_tools_from_capabilities(self) -> list[str]:
        if not self.client_capabilities:
            return []

        disabled: list[str] = []

        if not self.client_capabilities.terminal:
            disabled.append("bash")

        if fs := self.client_capabilities.fs:
            if not fs.readTextFile:
                disabled.append("read_file")
            if not fs.writeTextFile:
                disabled.append("write_file")
                disabled.append("search_replace")

        return disabled

    def _create_approval_callback(self, session_id: str) -> AsyncApprovalCallback:

        async def approval_callback(
            tool_name: str, args: dict[str, Any], tool_call_id: str
        ) -> tuple[str, str | None]:
            # Create the tool call update
            tool_call = ToolCall(toolCallId=tool_call_id)

            # Request permission from the user
            request = RequestPermissionRequest(
                sessionId=session_id, toolCall=tool_call, options=TOOL_OPTIONS
            )

            response = await self.connection.requestPermission(request)

            # Parse the response using isinstance for proper type narrowing
            if response.outcome.outcome == "selected":
                outcome = cast(AllowedOutcome, response.outcome)
                return self._handle_permission_selection(outcome.optionId)
            else:
                return (
                    "n",
                    str(
                        get_user_cancellation_message(
                            CancellationReason.OPERATION_CANCELLED
                        )
                    ),
                )

        return approval_callback

    @staticmethod
    def _handle_permission_selection(option_id: str) -> tuple[str, str | None]:
        match option_id:
            case ToolOption.ALLOW_ONCE:
                return ("y", None)
            case ToolOption.ALLOW_ALWAYS:
                return ("a", None)
            case ToolOption.REJECT_ONCE:
                return ("n", "User rejected the tool call, provide an alternative plan")
            case _:
                return ("n", f"Unknown option: {option_id}")

    def _get_session(self, session_id: str) -> AcpSession:
        if session_id not in self.sessions:
            raise RequestError.invalid_params({"session": "Not found"})
        return self.sessions[session_id]

    @override
    async def loadSession(self, params: LoadSessionRequest) -> None:
        raise NotImplementedError()

    @override
    async def setSessionMode(
        self, params: SetSessionModeRequest
    ) -> SetSessionModeResponse | None:
        session = self._get_session(params.sessionId)

        if not VibeSessionMode.is_valid(params.modeId):
            return None

        session.mode_id = VibeSessionMode(params.modeId)
        session.agent.auto_approve = params.modeId == VibeSessionMode.AUTO_APPROVE

        return SetSessionModeResponse()

    @override
    async def setSessionModel(
        self, params: SetSessionModelRequest
    ) -> SetSessionModelResponse | None:
        session = self._get_session(params.sessionId)

        model_aliases = [model.alias for model in session.agent.config.models]
        if params.modelId not in model_aliases:
            return None

        VibeConfig.save_updates({"active_model": params.modelId})

        new_config = VibeConfig.load(
            workdir=session.agent.config.workdir,
            tool_paths=session.agent.config.tool_paths,
            disabled_tools=self._get_disabled_tools_from_capabilities(),
        )

        await session.agent.reload_with_initial_messages(config=new_config)

        return SetSessionModelResponse()

    @override
    async def prompt(self, params: PromptRequest) -> PromptResponse:
        session = self._get_session(params.sessionId)

        if session.task is not None:
            raise RuntimeError(
                "Concurrent prompts are not supported yet, wait for agent to finish"
            )

        text_prompt = self._build_text_prompt(params.prompt)

        async def agent_task() -> None:
            async for update in self._run_agent_loop(session, text_prompt):
                await self.connection.sessionUpdate(
                    SessionNotification(sessionId=session.id, update=update)
                )

        try:
            session.task = asyncio.create_task(agent_task())
            await session.task

        except asyncio.CancelledError:
            return PromptResponse(stopReason="cancelled")

        except Exception as e:
            await self.connection.sessionUpdate(
                SessionNotification(
                    sessionId=params.sessionId,
                    update=AgentMessageChunk(
                        sessionUpdate="agent_message_chunk",
                        content=TextContentBlock(type="text", text=f"Error: {e!s}"),
                    ),
                )
            )

            return PromptResponse(stopReason="refusal")

        finally:
            session.task = None

        return PromptResponse(stopReason="end_turn")

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
                        "mimeType": block.mimeType,
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
                    raise ValueError(f"Unsupported content block type: {block.type}")
        return text_prompt

    async def _run_agent_loop(
        self, session: AcpSession, prompt: str
    ) -> AsyncGenerator[SessionUpdate]:
        rendered_prompt = render_path_prompt(
            prompt, base_dir=session.agent.config.effective_workdir
        )
        async for event in session.agent.act(rendered_prompt):
            if isinstance(event, AssistantEvent):
                yield AgentMessageChunk(
                    sessionUpdate="agent_message_chunk",
                    content=TextContentBlock(type="text", text=event.content),
                )

            elif isinstance(event, ToolCallEvent):
                if issubclass(event.tool_class, BaseAcpTool):
                    event.tool_class.update_tool_state(
                        tool_manager=session.agent.tool_manager,
                        connection=self.connection,
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

    @override
    async def cancel(self, params: CancelNotification) -> None:
        session = self._get_session(params.sessionId)
        if session.task and not session.task.done():
            session.task.cancel()
            session.task = None

    @override
    async def extMethod(self, method: str, params: dict) -> dict:
        raise NotImplementedError()

    @override
    async def extNotification(self, method: str, params: dict) -> None:
        raise NotImplementedError()


async def _run_acp_server() -> None:
    reader, writer = await stdio_streams()

    AgentSideConnection(lambda connection: VibeAcpAgent(connection), writer, reader)
    await asyncio.Event().wait()


def run_acp_server() -> None:
    try:
        asyncio.run(_run_acp_server())
    except KeyboardInterrupt:
        # This is expected when the server is terminated
        pass
    except Exception as e:
        # Log any unexpected errors
        print(f"ACP Agent Server error: {e}", file=sys.stderr)
        raise
