from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import os
from pathlib import Path
import sys
from typing import Any, cast, override

from acp import (
    PROTOCOL_VERSION,
    Agent as AcpAgent,
    Client,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    RequestError,
    SetSessionModelResponse,
    SetSessionModeResponse,
    run_agent,
)
from acp.helpers import ContentBlock, SessionUpdate
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    AuthenticateResponse,
    AuthMethod,
    ClientCapabilities,
    ContentToolCallContent,
    ForkSessionResponse,
    HttpMcpServer,
    Implementation,
    ListSessionsResponse,
    McpServerStdio,
    ModelInfo,
    PromptCapabilities,
    ResumeSessionResponse,
    SessionModelState,
    SessionModeState,
    SseMcpServer,
    TextContentBlock,
    TextResourceContents,
    ToolCallProgress,
    ToolCallUpdate,
    UserMessageChunk,
)
from pydantic import BaseModel, ConfigDict

from vibe import VIBE_ROOT, __version__
from vibe.acp.tools.base import BaseAcpTool
from vibe.acp.tools.session_update import (
    tool_call_session_update,
    tool_result_session_update,
)
from vibe.acp.utils import (
    TOOL_OPTIONS,
    ToolOption,
    create_compact_end_session_update,
    create_compact_start_session_update,
    get_all_acp_session_modes,
    is_valid_acp_agent,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.autocompletion.path_prompt_adapter import render_path_prompt
from vibe.core.config import MissingAPIKeyError, VibeConfig, load_dotenv_values
from vibe.core.tools.base import BaseToolConfig, ToolPermission
from vibe.core.types import (
    ApprovalResponse,
    AssistantEvent,
    AsyncApprovalCallback,
    CompactEndEvent,
    CompactStartEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
)
from vibe.core.utils import CancellationReason, get_user_cancellation_message


class AcpSessionLoop(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    id: str
    agent_loop: AgentLoop
    task: asyncio.Task[None] | None = None


class VibeAcpAgentLoop(AcpAgent):
    client: Client

    def __init__(self) -> None:
        self.sessions: dict[str, AcpSessionLoop] = {}
        self.client_capabilities = None

    @override
    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        self.client_capabilities = client_capabilities

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
            agent_capabilities=AgentCapabilities(
                load_session=False,
                prompt_capabilities=PromptCapabilities(
                    audio=False, embedded_context=True, image=False
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
        raise NotImplementedError("Not implemented yet")

    @override
    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        load_dotenv_values()
        os.chdir(cwd)

        try:
            config = VibeConfig.load(disabled_tools=["ask_user_question"])
            config.tool_paths.extend(self._get_acp_tool_overrides())
        except MissingAPIKeyError as e:
            raise RequestError.auth_required({
                "message": "You must be authenticated before creating a new session"
            }) from e

        agent_loop = AgentLoop(
            config=config, agent_name=BuiltinAgentName.DEFAULT, enable_streaming=True
        )
        # NOTE: For now, we pin session.id to agent_loop.session_id right after init time.
        # We should just use agent_loop.session_id everywhere, but it can still change during
        # session lifetime (e.g. agent_loop.compact is called).
        # We should refactor agent_loop.session_id to make it immutable in ACP context.
        session = AcpSessionLoop(id=agent_loop.session_id, agent_loop=agent_loop)
        self.sessions[session.id] = session

        if not agent_loop.auto_approve:
            agent_loop.set_approval_callback(
                self._create_approval_callback(agent_loop.session_id)
            )

        response = NewSessionResponse(
            session_id=agent_loop.session_id,
            models=SessionModelState(
                current_model_id=agent_loop.config.active_model,
                available_models=[
                    ModelInfo(model_id=model.alias, name=model.alias)
                    for model in agent_loop.config.models
                ],
            ),
            modes=SessionModeState(
                current_mode_id=session.agent_loop.agent_profile.name,
                available_modes=get_all_acp_session_modes(agent_loop.agent_manager),
            ),
        )
        return response

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

    def _create_approval_callback(self, session_id: str) -> AsyncApprovalCallback:
        session = self._get_session(session_id)

        def _handle_permission_selection(
            option_id: str, tool_name: str
        ) -> tuple[ApprovalResponse, str | None]:
            match option_id:
                case ToolOption.ALLOW_ONCE:
                    return (ApprovalResponse.YES, None)
                case ToolOption.ALLOW_ALWAYS:
                    if tool_name not in session.agent_loop.config.tools:
                        session.agent_loop.config.tools[tool_name] = BaseToolConfig()
                    session.agent_loop.config.tools[
                        tool_name
                    ].permission = ToolPermission.ALWAYS
                    return (ApprovalResponse.YES, None)
                case ToolOption.REJECT_ONCE:
                    return (
                        ApprovalResponse.NO,
                        "User rejected the tool call, provide an alternative plan",
                    )
                case _:
                    return (ApprovalResponse.NO, f"Unknown option: {option_id}")

        async def approval_callback(
            tool_name: str, args: BaseModel, tool_call_id: str
        ) -> tuple[ApprovalResponse, str | None]:
            # Create the tool call update
            tool_call = ToolCallUpdate(tool_call_id=tool_call_id)

            response = await self.client.request_permission(
                session_id=session_id, tool_call=tool_call, options=TOOL_OPTIONS
            )

            # Parse the response using isinstance for proper type narrowing
            if response.outcome.outcome == "selected":
                outcome = cast(AllowedOutcome, response.outcome)
                return _handle_permission_selection(outcome.option_id, tool_name)
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
            raise RequestError.invalid_params({"session": "Not found"})
        return self.sessions[session_id]

    @override
    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        raise NotImplementedError()

    @override
    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        session = self._get_session(session_id)

        if not is_valid_acp_agent(session.agent_loop.agent_manager, mode_id):
            return None

        await session.agent_loop.switch_agent(mode_id)

        if session.agent_loop.auto_approve:
            session.agent_loop.approval_callback = None
        else:
            session.agent_loop.set_approval_callback(
                self._create_approval_callback(session.id)
            )

        return SetSessionModeResponse()

    @override
    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        session = self._get_session(session_id)

        model_aliases = [model.alias for model in session.agent_loop.config.models]
        if model_id not in model_aliases:
            return None

        VibeConfig.save_updates({"active_model": model_id})

        new_config = VibeConfig.load(
            tool_paths=session.agent_loop.config.tool_paths,
            disabled_tools=["ask_user_question"],
        )

        await session.agent_loop.reload_with_initial_messages(base_config=new_config)

        return SetSessionModelResponse()

    @override
    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        raise NotImplementedError()

    @override
    async def prompt(
        self, prompt: list[ContentBlock], session_id: str, **kwargs: Any
    ) -> PromptResponse:
        session = self._get_session(session_id)

        if session.task is not None:
            raise RuntimeError(
                "Concurrent prompts are not supported yet, wait for agent loop to finish"
            )

        text_prompt = self._build_text_prompt(prompt)

        temp_user_message_id: str | None = kwargs.get("messageId")

        async def agent_loop_task() -> None:
            async for update in self._run_agent_loop(
                session, text_prompt, temp_user_message_id
            ):
                await self.client.session_update(session_id=session.id, update=update)

        try:
            session.task = asyncio.create_task(agent_loop_task())
            await session.task

        except asyncio.CancelledError:
            return PromptResponse(stop_reason="cancelled")

        except Exception as e:
            await self.client.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=f"Error: {e!s}"),
                ),
            )

            return PromptResponse(stop_reason="refusal")

        finally:
            session.task = None

        return PromptResponse(stop_reason="end_turn")

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
                    raise ValueError(f"Unsupported content block type: {block.type}")
        return text_prompt

    async def _run_agent_loop(
        self, session: AcpSessionLoop, prompt: str, user_message_id: str | None = None
    ) -> AsyncGenerator[SessionUpdate]:
        rendered_prompt = render_path_prompt(prompt, base_dir=Path.cwd())

        async for event in session.agent_loop.act(rendered_prompt):
            if isinstance(event, UserMessageEvent):
                yield UserMessageChunk(
                    session_update="user_message_chunk",
                    content=TextContentBlock(type="text", text=""),
                    field_meta={
                        "messageId": event.message_id,
                        **(
                            {"previousMessageId": user_message_id}
                            if user_message_id
                            else {}
                        ),
                    },
                )

            elif isinstance(event, AssistantEvent):
                yield AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=event.content),
                    field_meta={"messageId": event.message_id},
                )

            elif isinstance(event, ReasoningEvent):
                yield AgentThoughtChunk(
                    session_update="agent_thought_chunk",
                    content=TextContentBlock(type="text", text=event.content),
                    field_meta={"messageId": event.message_id},
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

            elif isinstance(event, ToolStreamEvent):
                yield ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=event.tool_call_id,
                    content=[
                        ContentToolCallContent(
                            type="content",
                            content=TextContentBlock(type="text", text=event.message),
                        )
                    ],
                )

            elif isinstance(event, CompactStartEvent):
                yield create_compact_start_session_update(event)

            elif isinstance(event, CompactEndEvent):
                yield create_compact_end_session_update(event)

    @override
    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self._get_session(session_id)
        if session.task and not session.task.done():
            session.task.cancel()
            session.task = None

    @override
    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        raise NotImplementedError()

    @override
    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        raise NotImplementedError()

    @override
    async def ext_method(self, method: str, params: dict) -> dict:
        raise NotImplementedError()

    @override
    async def ext_notification(self, method: str, params: dict) -> None:
        raise NotImplementedError()

    @override
    def on_connect(self, conn: Client) -> None:
        self.client = conn


def run_acp_server() -> None:
    try:
        asyncio.run(run_agent(agent=VibeAcpAgentLoop(), use_unstable_protocol=True))
    except KeyboardInterrupt:
        # This is expected when the server is terminated
        pass
    except Exception as e:
        # Log any unexpected errors
        print(f"ACP Agent Server error: {e}", file=sys.stderr)
        raise
