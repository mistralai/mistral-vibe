"""Client-side launcher for the client-e2e replay, gated by an env var.

When ``VIBE_REPLAY_FIXTURE`` is set (only client-e2e sets it), the
frontend connects to the standalone ``vibe-replay`` binary named by
``VIBE_REPLAY_BIN`` over stdio instead of running the real app-server. The
binary answers the handshake and streams a canned, byte-stable event stream (see
``vibe/cli-rust/crates/replay``), so both frontends can be diffed for rendering parity
without a model, loop, or tools -- and without starting a Python interpreter just
to replay.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
import json
import os
from typing import Any

from vibe.app_server.client import AppServerClient
from vibe.app_server.transport import _decode_message

REPLAY_FIXTURE_ENV_VAR = "VIBE_REPLAY_FIXTURE"
REPLAY_BIN_ENV_VAR = "VIBE_REPLAY_BIN"

# StreamReader line limit: a single JSON-RPC frame (e.g. session/snapshot) can be
# far larger than asyncio's 64 KiB default, so raise it to avoid a split frame.
_READ_LIMIT = 16 * 1024 * 1024


def replay_launch_command() -> list[str] | None:
    """The ``vibe-replay`` command when replay is active, else ``None``."""
    if not os.environ.get(REPLAY_FIXTURE_ENV_VAR):
        return None
    bin_path = os.environ.get(REPLAY_BIN_ENV_VAR)
    if not bin_path:
        raise RuntimeError(
            f"{REPLAY_FIXTURE_ENV_VAR} is set but {REPLAY_BIN_ENV_VAR} is not"
        )
    return [bin_path]


def make_replay_client() -> AppServerClient:
    """An ``AppServerClient`` wired to a freshly spawned replay subprocess."""
    command = replay_launch_command()
    if command is None:
        raise RuntimeError("replay is not active")
    return AppServerClient(SubprocessJsonRpcTransport(command))


class SubprocessJsonRpcTransport:
    """Frame NDJSON over a spawned subprocess' stdio; spawn lazily on first use."""

    def __init__(self, command: list[str]) -> None:
        self._command = command
        self._process: asyncio.subprocess.Process | None = None
        self._spawn_lock = asyncio.Lock()
        self._closed = False

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._process is not None:
            return self._process
        async with self._spawn_lock:
            if self._process is None:
                self._process = await asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    limit=_READ_LIMIT,
                )
            return self._process

    async def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("JSON-RPC transport is closed")
        process = await self._ensure_process()
        assert process.stdin is not None
        process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        await process.stdin.drain()

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        process = await self._ensure_process()
        assert process.stdout is not None
        while raw := await process.stdout.readline():
            yield _decode_message(raw)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        with suppress(ProcessLookupError):
            process.terminate()
        with suppress(Exception):
            await process.wait()
