from __future__ import annotations

from typing import Protocol

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


class ClientToolHandler(Protocol):
    async def read_text_file(
        self, params: ClientToolReadTextFileParams
    ) -> ClientToolReadTextFileResponse: ...

    async def write_text_file(
        self, params: ClientToolWriteTextFileParams
    ) -> EmptyResponse: ...

    async def create_terminal(
        self, params: ClientToolTerminalCreateParams
    ) -> ClientToolTerminalCreateResponse: ...

    async def wait_for_terminal_exit(
        self, params: ClientToolTerminalParams
    ) -> ClientToolTerminalWaitResponse: ...

    async def terminal_output(
        self, params: ClientToolTerminalParams
    ) -> ClientToolTerminalOutputResponse: ...

    async def kill_terminal(
        self, params: ClientToolTerminalParams
    ) -> EmptyResponse: ...

    async def release_terminal(
        self, params: ClientToolTerminalParams
    ) -> EmptyResponse: ...
