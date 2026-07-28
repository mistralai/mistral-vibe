from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from vibe.app_server._model import ProtocolModel
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientToolMethod,
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
from vibe.core.tools.io_port import ShellCommandRequest, ShellCommandResult, ToolIOPort
from vibe.observability.logging import logger
from vibe.utils.io import BoundedReadResult, ReadSafeResult, normalize_newlines

_TERMINAL_CLEANUP_TIMEOUT = 10.0


class RequestClientResult(Protocol):
    async def __call__[ResultT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResultT]
    ) -> ResultT: ...


class ClientToolIO(ToolIOPort):
    def __init__(
        self,
        request_client: RequestClientResult,
        capabilities: Callable[[], ClientCapabilities],
        root_session_id: Callable[[], str],
    ) -> None:
        self._request_client = request_client
        self._capabilities = capabilities
        self._root_session_id = root_session_id

    @property
    def supports_read(self) -> bool:
        return "filesystem/read" in self._capabilities().client_tools

    @property
    def supports_write(self) -> bool:
        return "filesystem/write" in self._capabilities().client_tools

    @property
    def supports_terminal(self) -> bool:
        return "terminal" in self._capabilities().client_tools

    async def read_lines(
        self, path: Path, *, start_line: int, limit: int, max_bytes: int
    ) -> BoundedReadResult:
        response = await self._request_client(
            ClientToolMethod.READ_TEXT_FILE,
            ClientToolReadTextFileParams(
                session_id=self._root_session_id(),
                path=str(path),
                line=start_line if start_line != 1 else None,
                limit=limit + 1,
            ),
            ClientToolReadTextFileResponse,
        )
        raw = response.content.encode("utf-8")
        byte_truncated = len(raw) > max_bytes
        content = (
            raw[:max_bytes].decode("utf-8", errors="replace")
            if byte_truncated
            else response.content
        )
        lines = content.splitlines()
        was_truncated = byte_truncated or len(lines) > limit
        total_lines = 0 if not response.content and start_line == 1 else None
        return BoundedReadResult(lines[:limit], total_lines, was_truncated)

    async def read_text(self, path: Path) -> ReadSafeResult:
        response = await self._request_client(
            ClientToolMethod.READ_TEXT_FILE,
            ClientToolReadTextFileParams(
                session_id=self._root_session_id(), path=str(path)
            ),
            ClientToolReadTextFileResponse,
        )
        content, newline = normalize_newlines(response.content)
        return ReadSafeResult(content, "utf-8", newline)

    async def write_text(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
        newline: str | None = None,
    ) -> None:
        del encoding
        if newline is not None:
            content = content.replace("\n", newline)
        await self._request_client(
            ClientToolMethod.WRITE_TEXT_FILE,
            ClientToolWriteTextFileParams(
                session_id=self._root_session_id(), path=str(path), content=content
            ),
            EmptyResponse,
        )

    async def run_shell(self, request: ShellCommandRequest) -> ShellCommandResult:
        root_session_id = self._root_session_id()
        created = await self._request_client(
            ClientToolMethod.TERMINAL_CREATE,
            ClientToolTerminalCreateParams(
                session_id=root_session_id,
                command=request.command,
                args=request.args,
                env=request.env,
                cwd=str(request.cwd),
                output_byte_limit=request.max_output_bytes,
                tool_call_id=(
                    request.tool_call_id
                    if request.session_id == root_session_id
                    else None
                ),
            ),
            ClientToolTerminalCreateResponse,
        )
        terminal = ClientToolTerminalParams(
            session_id=root_session_id, terminal_id=created.terminal_id
        )
        try:
            try:
                exit_status = await asyncio.wait_for(
                    self._request_client(
                        ClientToolMethod.TERMINAL_WAIT,
                        terminal,
                        ClientToolTerminalWaitResponse,
                    ),
                    timeout=request.timeout,
                )
            except TimeoutError:
                await self._cleanup(ClientToolMethod.TERMINAL_KILL, terminal)
                raise
            except BaseException:
                await self._cleanup(ClientToolMethod.TERMINAL_KILL, terminal)
                raise
            output = await self._request_client(
                ClientToolMethod.TERMINAL_OUTPUT,
                terminal,
                ClientToolTerminalOutputResponse,
            )
            if exit_status.exit_code is None and exit_status.signal is None:
                raise RuntimeError("Client terminal returned no exit status")
            returncode = (
                exit_status.exit_code if exit_status.exit_code is not None else -1
            )
            stderr = (
                f"Process terminated by {exit_status.signal}"
                if exit_status.signal is not None
                else ""
            )
            return ShellCommandResult(
                stdout=output.output, stderr=stderr, returncode=returncode
            )
        finally:
            await self._cleanup(ClientToolMethod.TERMINAL_RELEASE, terminal)

    async def _cleanup(self, method: str, params: ClientToolTerminalParams) -> None:
        try:
            await asyncio.wait_for(
                self._request_client(method, params, EmptyResponse),
                timeout=_TERMINAL_CLEANUP_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(
                "Client terminal cleanup failed method=%s terminal_id=%s",
                method,
                params.terminal_id,
                exc_info=exc,
            )
