from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import sys
from typing import Any, Protocol


class InvalidJsonRpcMessage(RuntimeError):
    pass


class JsonRpcTransport(Protocol):
    async def send(self, message: dict[str, Any]) -> None: ...

    def messages(self) -> AsyncIterator[dict[str, Any]]: ...

    async def close(self) -> None: ...


class BinaryLineReader(Protocol):
    def readline(self, /) -> bytes: ...


class BinaryLineWriter(Protocol):
    def write(self, data: bytes, /) -> int: ...

    def flush(self, /) -> None: ...


class StdioJsonRpcTransport:
    def __init__(
        self,
        reader: BinaryLineReader,
        writer: BinaryLineWriter,
        *,
        outbox_size: int = 256,
    ) -> None:
        if outbox_size < 1:
            raise ValueError("outbox_size must be positive")
        self._reader = reader
        self._writer = writer
        self._outbox: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=outbox_size)
        self._writer_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False

    @classmethod
    def from_standard_streams(cls) -> StdioJsonRpcTransport:
        return cls(sys.stdin.buffer, sys.stdout.buffer)

    async def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("JSON-RPC transport is closed")
        writer_task = self._ensure_writer()
        line = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        await self._enqueue(line, writer_task)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while raw := await asyncio.to_thread(self._reader.readline):
            yield _decode_message(raw)

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    def _ensure_writer(self) -> asyncio.Task[None]:
        if self._writer_task is None:
            self._writer_task = asyncio.create_task(self._write_messages())
        self._check_writer(self._writer_task)
        return self._writer_task

    async def _finish_close(self) -> None:
        writer_task = self._writer_task
        if writer_task is None:
            return
        await self._enqueue(None, writer_task, allow_writer_exit=True)
        await asyncio.shield(writer_task)

    async def _enqueue(
        self,
        item: bytes | None,
        writer_task: asyncio.Task[None],
        *,
        allow_writer_exit: bool = False,
    ) -> None:
        self._check_writer(writer_task, allow_stopped=allow_writer_exit)
        put_task = asyncio.create_task(self._outbox.put(item))
        try:
            done, _ = await asyncio.wait(
                (put_task, writer_task), return_when=asyncio.FIRST_COMPLETED
            )
            if writer_task in done:
                self._check_writer(writer_task, allow_stopped=allow_writer_exit)
            await put_task
            self._check_writer(writer_task, allow_stopped=allow_writer_exit)
        finally:
            if not put_task.done():
                put_task.cancel()
                try:
                    await put_task
                except asyncio.CancelledError:
                    pass

    @staticmethod
    def _check_writer(
        writer_task: asyncio.Task[None], *, allow_stopped: bool = False
    ) -> None:
        if not writer_task.done():
            return
        try:
            writer_task.result()
        except asyncio.CancelledError as exc:
            raise RuntimeError("JSON-RPC writer was cancelled") from exc
        if not allow_stopped:
            raise RuntimeError("JSON-RPC writer stopped unexpectedly")

    async def _write_messages(self) -> None:
        while (line := await self._outbox.get()) is not None:
            await asyncio.to_thread(self._write_line, line)

    def _write_line(self, line: bytes) -> None:
        self._writer.write(line)
        self._writer.flush()


class MemoryJsonRpcTransport:
    def __init__(
        self, incoming: asyncio.Queue[str | None], outgoing: asyncio.Queue[str | None]
    ) -> None:
        self._incoming = incoming
        self._outgoing = outgoing
        self._closed = False

    async def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("JSON-RPC transport is closed")
        await self._outgoing.put(json.dumps(message, separators=(",", ":")))

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while (raw := await self._incoming.get()) is not None:
            yield _decode_message(raw)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._outgoing.put(None)


def memory_transport_pair() -> tuple[MemoryJsonRpcTransport, MemoryJsonRpcTransport]:
    client_incoming: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
    server_incoming: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
    return (
        MemoryJsonRpcTransport(client_incoming, server_incoming),
        MemoryJsonRpcTransport(server_incoming, client_incoming),
    )


def _decode_message(raw: str | bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidJsonRpcMessage("Malformed JSON-RPC message") from exc
    if not isinstance(value, dict):
        raise InvalidJsonRpcMessage("JSON-RPC messages must be objects")
    return value
