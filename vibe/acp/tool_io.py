from __future__ import annotations

from acp import Client
from acp.helpers import tool_terminal_ref
from acp.schema import EnvVariable, ToolCallProgress

from vibe.app_server.client_tools import ClientToolHandler
from vibe.app_server.protocol import (
    ClientToolReadTextFileParams,
    ClientToolReadTextFileResponse,
    ClientToolTerminalCreateParams,
    ClientToolTerminalCreateResponse,
    ClientToolTerminalOutputResponse,
    ClientToolTerminalParams,
    ClientToolTerminalWaitResponse,
    ClientToolWriteTextFileParams,
    EmptyResponse,
)


class AcpClientToolHandler(ClientToolHandler):
    def __init__(self, client: Client) -> None:
        self._client = client
        self._session_id: str | None = None

    def bind_session(self, session_id: str) -> None:
        if self._session_id is not None and self._session_id != session_id:
            raise RuntimeError("ACP client tool handler is already bound")
        self._session_id = session_id

    def _require_session_id(self) -> str:
        if self._session_id is None:
            raise RuntimeError("ACP client tool handler is not bound to a session")
        return self._session_id

    async def read_text_file(
        self, params: ClientToolReadTextFileParams
    ) -> ClientToolReadTextFileResponse:
        response = await self._client.read_text_file(
            session_id=self._require_session_id(),
            path=params.path,
            line=params.line,
            limit=params.limit,
        )
        return ClientToolReadTextFileResponse(content=response.content)

    async def write_text_file(
        self, params: ClientToolWriteTextFileParams
    ) -> EmptyResponse:
        await self._client.write_text_file(
            session_id=self._require_session_id(),
            path=params.path,
            content=params.content,
        )
        return EmptyResponse()

    async def create_terminal(
        self, params: ClientToolTerminalCreateParams
    ) -> ClientToolTerminalCreateResponse:
        response = await self._client.create_terminal(
            session_id=self._require_session_id(),
            command=params.command,
            args=params.args,
            env=(
                [
                    EnvVariable(name=name, value=value)
                    for name, value in params.env.items()
                ]
                if params.env is not None
                else None
            ),
            cwd=params.cwd,
            output_byte_limit=params.output_byte_limit,
        )
        if params.tool_call_id is not None:
            await self._client.session_update(
                session_id=self._require_session_id(),
                update=ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=params.tool_call_id,
                    kind="execute",
                    status="in_progress",
                    content=[tool_terminal_ref(response.terminal_id)],
                ),
            )
        return ClientToolTerminalCreateResponse(terminal_id=response.terminal_id)

    async def wait_for_terminal_exit(
        self, params: ClientToolTerminalParams
    ) -> ClientToolTerminalWaitResponse:
        response = await self._client.wait_for_terminal_exit(
            session_id=self._require_session_id(), terminal_id=params.terminal_id
        )
        return ClientToolTerminalWaitResponse(
            exit_code=response.exit_code, signal=response.signal
        )

    async def terminal_output(
        self, params: ClientToolTerminalParams
    ) -> ClientToolTerminalOutputResponse:
        response = await self._client.terminal_output(
            session_id=self._require_session_id(), terminal_id=params.terminal_id
        )
        return ClientToolTerminalOutputResponse(
            output=response.output, truncated=response.truncated
        )

    async def kill_terminal(self, params: ClientToolTerminalParams) -> EmptyResponse:
        await self._client.kill_terminal(
            session_id=self._require_session_id(), terminal_id=params.terminal_id
        )
        return EmptyResponse()

    async def release_terminal(self, params: ClientToolTerminalParams) -> EmptyResponse:
        await self._client.release_terminal(
            session_id=self._require_session_id(), terminal_id=params.terminal_id
        )
        return EmptyResponse()
