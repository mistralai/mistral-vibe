from __future__ import annotations

from collections.abc import Callable
from typing import Any

from acp import (
    Agent,
    AgentSideConnection,
    CreateTerminalRequest,
    KillTerminalCommandRequest,
    KillTerminalCommandResponse,
    ReadTextFileRequest,
    ReadTextFileResponse,
    ReleaseTerminalRequest,
    ReleaseTerminalResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionNotification,
    TerminalHandle,
    TerminalOutputRequest,
    TerminalOutputResponse,
    WaitForTerminalExitRequest,
    WaitForTerminalExitResponse,
    WriteTextFileRequest,
    WriteTextFileResponse,
)


class FakeAgentSideConnection(AgentSideConnection):
    def __init__(self, to_agent: Callable[[AgentSideConnection], Agent]) -> None:
        self._session_updates = []
        to_agent(self)

    async def sessionUpdate(self, params: SessionNotification) -> None:
        self._session_updates.append(params)

    async def requestPermission(
        self, params: RequestPermissionRequest
    ) -> RequestPermissionResponse:
        raise NotImplementedError()

    async def readTextFile(self, params: ReadTextFileRequest) -> ReadTextFileResponse:
        raise NotImplementedError()

    async def writeTextFile(
        self, params: WriteTextFileRequest
    ) -> WriteTextFileResponse | None:
        raise NotImplementedError()

    async def createTerminal(self, params: CreateTerminalRequest) -> TerminalHandle:
        raise NotImplementedError()

    async def terminalOutput(
        self, params: TerminalOutputRequest
    ) -> TerminalOutputResponse:
        raise NotImplementedError()

    async def releaseTerminal(
        self, params: ReleaseTerminalRequest
    ) -> ReleaseTerminalResponse | None:
        raise NotImplementedError()

    async def waitForTerminalExit(
        self, params: WaitForTerminalExitRequest
    ) -> WaitForTerminalExitResponse:
        raise NotImplementedError()

    async def killTerminal(
        self, params: KillTerminalCommandRequest
    ) -> KillTerminalCommandResponse | None:
        raise NotImplementedError()

    async def extMethod(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError()

    async def extNotification(self, method: str, params: dict[str, Any]) -> None:
        raise NotImplementedError()

    async def close(self) -> None:
        raise NotImplementedError()

    async def __aenter__(self) -> AgentSideConnection:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
