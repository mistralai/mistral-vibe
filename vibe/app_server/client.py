from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ClientInfo,
    InitializeParams,
    InitializeResponse,
    JsonRpcErrorResponse,
    JsonRpcProtocolError,
    JsonRpcSuccessResponse,
    Notification,
    ProtocolError,
    ServerRequest,
    protocol_value,
    validate_json_rpc_envelope,
)
from vibe.app_server.transport import JsonRpcTransport


class AppServerConnectionClosed(RuntimeError):
    pass


type ServerMessage = Notification | ServerRequest


@dataclass(frozen=True, slots=True)
class _IncomingMessage:
    sequence: int
    message: ServerMessage


@dataclass(frozen=True, slots=True)
class _ClientResponse:
    result: dict[str, Any] | None
    error: Exception | None
    incoming_sequence: int


class AppServerClient:
    def __init__(
        self,
        transport: JsonRpcTransport,
        *,
        run_peer: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._transport = transport
        self._run_peer = run_peer
        self._peer_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._next_request_id = 1
        self._pending: dict[str, asyncio.Future[_ClientResponse]] = {}
        self._abandoned_request_ids: set[str] = set()
        self._incoming: asyncio.Queue[_IncomingMessage] = asyncio.Queue(maxsize=256)
        self._incoming_closed = asyncio.Event()
        self._incoming_error: Exception | None = None
        self._received_sequence = 0
        self._processed_sequence = 0
        self._processed = asyncio.Condition()
        self._closed = False

    async def start(self) -> None:
        self._ensure_open()
        if self._reader_task is not None:
            return
        if self._run_peer is not None:
            self._peer_task = asyncio.create_task(self._run_peer())
        self._reader_task = asyncio.create_task(self._read_messages())

    async def initialize(
        self, client_info: ClientInfo, capabilities: ClientCapabilities | None = None
    ) -> InitializeResponse:
        result = await self.request(
            "initialize",
            InitializeParams(
                client_info=client_info,
                capabilities=capabilities or ClientCapabilities(),
            ),
        )
        return validate_wire(InitializeResponse, result)

    async def notify(
        self, method: str, params: ProtocolModel | dict[str, Any] | None = None
    ) -> None:
        await self.start()
        await self._transport.send({
            "jsonrpc": "2.0",
            "method": method,
            "params": protocol_value(params),
        })

    async def request(
        self,
        method: str,
        params: ProtocolModel | dict[str, Any] | None = None,
        *,
        wait_for_incoming: bool = False,
    ) -> dict[str, Any]:
        await self.start()
        request_id = f"client-{self._next_request_id}"
        self._next_request_id += 1
        future: asyncio.Future[_ClientResponse] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        sent = False
        try:
            await self._transport.send({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": protocol_value(params),
            })
            sent = True
            response = await future
            if wait_for_incoming:
                await self._wait_until_processed(response.incoming_sequence)
            if response.error is not None:
                raise response.error
            return response.result or {}
        except asyncio.CancelledError:
            if sent and future.cancelled():
                self._abandoned_request_ids.add(request_id)
            raise
        finally:
            self._pending.pop(request_id, None)

    async def respond(
        self,
        request_id: int | str,
        result: ProtocolModel | dict[str, Any] | None = None,
        *,
        error: ProtocolError | None = None,
    ) -> None:
        self._ensure_open()
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            message["error"] = error.model_dump(mode="json", by_alias=True)
        else:
            message["result"] = protocol_value(result)
        await self._transport.send(message)

    async def incoming(self) -> AsyncGenerator[ServerMessage, None]:
        while True:
            if self._incoming.empty() and self._incoming_closed.is_set():
                if self._incoming_error is not None:
                    raise self._incoming_error
                return
            incoming = await self._next_incoming()
            if incoming is None:
                continue
            try:
                yield incoming.message
            finally:
                async with self._processed:
                    self._processed_sequence = incoming.sequence
                    self._processed.notify_all()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._transport.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader_task
        if self._peer_task is not None:
            with suppress(asyncio.CancelledError):
                await self._peer_task
        self._fail_pending(AppServerConnectionClosed("App server client closed"))
        self._abandoned_request_ids.clear()
        await self._close_incoming(None)

    async def _read_messages(self) -> None:
        error: Exception | None = None
        try:
            async for message in self._transport.messages():
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = AppServerConnectionClosed("App server connection failed")
            error.__cause__ = exc
        finally:
            if error is None and not self._closed:
                error = AppServerConnectionClosed("App server connection closed")
            self._fail_pending(
                error or AppServerConnectionClosed("App server client closed")
            )
            self._abandoned_request_ids.clear()
            await self._close_incoming(error)

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _dispatch(self, message: dict[str, Any]) -> None:
        envelope = validate_json_rpc_envelope(message)
        if isinstance(envelope, ServerRequest | Notification):
            await self._put_incoming(envelope)
            return
        response: JsonRpcErrorResponse | JsonRpcSuccessResponse = envelope
        request_id = response.id
        if not isinstance(request_id, str):
            raise JsonRpcProtocolError("Client request responses require string IDs")
        future = self._pending.pop(request_id, None)
        if future is not None and future.cancelled():
            self._abandoned_request_ids.discard(request_id)
            return
        if future is None and request_id in self._abandoned_request_ids:
            self._abandoned_request_ids.remove(request_id)
            return
        if future is None or future.done():
            raise JsonRpcProtocolError(
                f"Response does not match a pending client request: {request_id}"
            )
        if isinstance(response, JsonRpcErrorResponse):
            future.set_result(
                _ClientResponse(
                    result=None,
                    error=AppServerResponseError(response.error),
                    incoming_sequence=self._received_sequence,
                )
            )
            return
        future.set_result(
            _ClientResponse(
                result=response.result,
                error=None,
                incoming_sequence=self._received_sequence,
            )
        )

    async def _put_incoming(self, message: ServerMessage) -> None:
        self._received_sequence += 1
        await self._incoming.put(
            _IncomingMessage(sequence=self._received_sequence, message=message)
        )

    async def _next_incoming(self) -> _IncomingMessage | None:
        message = asyncio.create_task(self._incoming.get())
        closed = asyncio.create_task(self._incoming_closed.wait())
        done, pending = await asyncio.wait(
            (message, closed), return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        if message in done:
            return message.result()
        if self._incoming.empty():
            return None
        return self._incoming.get_nowait()

    async def _wait_until_processed(self, sequence: int) -> None:
        async with self._processed:
            await self._processed.wait_for(
                lambda: (
                    self._processed_sequence >= sequence
                    or self._incoming_closed.is_set()
                )
            )
        if self._processed_sequence >= sequence:
            return
        raise self._incoming_error or AppServerConnectionClosed(
            "App server connection closed"
        )

    async def _close_incoming(self, error: Exception | None) -> None:
        if error is not None and self._incoming_error is None:
            self._incoming_error = error
        self._incoming_closed.set()
        async with self._processed:
            self._processed.notify_all()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("App server client is closed")
        if self._incoming_closed.is_set():
            raise self._incoming_error or AppServerConnectionClosed(
                "App server connection closed"
            )
