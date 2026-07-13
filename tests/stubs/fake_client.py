from __future__ import annotations

from typing import Any

from acp import (
    Agent as AcpAgent,
    Client,
    CreateElicitationResponse,
    CreateTerminalResponse,
    KillTerminalResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SessionNotification,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from acp.schema import (
    AgentMessageChunk,
    AgentPlanContentUpdate,
    AgentPlanRemovedUpdate,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    EnvVariable,
    PermissionOption,
    SessionInfoUpdate,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    UserMessageChunk,
)


class FakeClient(Client):
    agent: AcpAgent

    def __init__(self) -> None:
        self._session_updates = []

    async def session_update(
        self,
        session_id: str,
        update: UserMessageChunk
        | AgentMessageChunk
        | AgentThoughtChunk
        | ToolCallStart
        | ToolCallProgress
        | AgentPlanUpdate
        | AgentPlanContentUpdate
        | AgentPlanRemovedUpdate
        | AvailableCommandsUpdate
        | CurrentModeUpdate
        | SessionInfoUpdate
        | ConfigOptionUpdate
        | UsageUpdate,
        **kwargs: Any,
    ) -> None:
        self._session_updates.append(
            SessionNotification(session_id=session_id, update=update)
        )

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        raise NotImplementedError()

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        raise NotImplementedError()

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        raise NotImplementedError()

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        raise NotImplementedError()

    async def create_elicitation(
        self, message: str, mode: Any, **kwargs: Any
    ) -> CreateElicitationResponse:
        raise NotImplementedError()

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        raise NotImplementedError()

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> TerminalOutputResponse:
        raise NotImplementedError()

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        raise NotImplementedError()

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        raise NotImplementedError()

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> KillTerminalResponse | None:
        raise NotImplementedError()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError()

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        raise NotImplementedError()

    async def close(self) -> None:
        raise NotImplementedError()

    def on_connect(self, conn: AcpAgent) -> None:
        self.agent = conn

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
