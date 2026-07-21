"""Vibe-session-side client: a persistent asyncio Unix-socket connection to the
overlay broker. Sends hello/state, receives control. Launches the overlay (and
retries) if none is up yet. The connection staying open IS this session's
membership; closing it deregisters the session.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import os
from uuid import uuid4

from vibe.cli.pawgress.launcher import spawn_overlay_candidate
from vibe.core.logger import logger
from vibe.core.pawgress.events import IslandState
from vibe.core.pawgress.protocol import (
    ByeMsg,
    ControlMsg,
    HelloMsg,
    Message,
    StateMsg,
    decode_line,
    encode_line,
)
from vibe.overlay.singleton import socket_path

_CONNECT_ATTEMPTS = 30
_CONNECT_DELAY = 0.1

_TERMINAL_NAMES = {
    "Apple_Terminal": "Terminal",
    "iTerm.app": "iTerm",
    "vscode": "VS Code",
}


def _detect_terminal() -> str:
    raw = os.environ.get("TERM_PROGRAM", "")
    if not raw:
        return ""
    return _TERMINAL_NAMES.get(raw, raw.replace(".app", "").capitalize())


class PawgressClient:
    def __init__(
        self, on_control: Callable[[ControlMsg], None], label: str, model: str = ""
    ) -> None:
        self.sid = uuid4().hex
        self._on_control = on_control
        self._label = label
        self._model = model
        self._terminal = _detect_terminal()
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        reader, writer = await self._connect_or_launch()
        if reader is None or writer is None:
            return
        self._writer = writer
        self._write(
            HelloMsg(
                sid=self.sid,
                label=self._label,
                pid=os.getpid(),
                terminal=self._terminal,
                model=self._model,
            )
        )
        self._reader_task = asyncio.create_task(self._read_loop(reader))

    async def _connect_or_launch(
        self,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        path = str(socket_path())
        launched = False
        for _ in range(_CONNECT_ATTEMPTS):
            try:
                return await asyncio.open_unix_connection(path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                if not launched:
                    spawn_overlay_candidate()
                    launched = True
                await asyncio.sleep(_CONNECT_DELAY)
        logger.warning("Pawgress overlay did not come up; continuing headless")
        return None, None

    def _write(self, msg: Message) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write(encode_line(msg).encode("utf-8"))
        except (RuntimeError, ConnectionError):
            self._writer = None

    def send_state(self, state: IslandState) -> None:
        self._write(StateMsg(sid=self.sid, state=state))

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                line = await reader.readline()
            except (ConnectionError, OSError):
                break
            if not line:
                break
            msg = decode_line(line.decode("utf-8", "replace"))
            if isinstance(msg, ControlMsg):
                self._on_control(msg)

    async def close(self) -> None:
        self._write(ByeMsg(sid=self.sid))
        if self._writer is not None:
            with contextlib.suppress(Exception):
                await self._writer.drain()
                self._writer.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
