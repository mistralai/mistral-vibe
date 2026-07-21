from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from vibe.cli.pawgress import client as client_mod
from vibe.core.pawgress.events import ControlAction, IslandState, IslandStatus
from vibe.core.pawgress.protocol import ControlMsg, HelloMsg, decode_line, encode_line


@pytest.fixture
def short_sock_dir():
    # AF_UNIX paths are capped (~104 bytes on macOS); pytest's tmp_path is too
    # long, so use a short dir under /tmp.
    path = Path("/tmp") / f"vibepw_{uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_client_connects_sends_hello_and_receives_control(
    short_sock_dir, monkeypatch
):
    sock_file = short_sock_dir / "o.sock"
    monkeypatch.setattr(client_mod, "socket_path", lambda: sock_file)
    # Server is already up here; the client must not need to launch one.
    monkeypatch.setattr(client_mod, "spawn_overlay_candidate", lambda: None)

    received: list = []
    got_control = asyncio.Event()

    async def handle(reader, writer):
        line = await reader.readline()
        received.append(decode_line(line.decode()))
        writer.write(encode_line(ControlMsg(action=ControlAction.STOP)).encode())
        await writer.drain()

    server = await asyncio.start_unix_server(handle, path=str(sock_file))

    controls: list = []

    def on_control(msg: ControlMsg) -> None:
        controls.append(msg)
        got_control.set()

    client = client_mod.PawgressClient(on_control=on_control, label="Fix bug")
    await client.connect()
    client.send_state(IslandState(goal="g", state=IslandStatus.WORKING))
    await asyncio.wait_for(got_control.wait(), timeout=2.0)

    assert isinstance(received[0], HelloMsg)
    assert received[0].label == "Fix bug"
    assert controls[0].action == ControlAction.STOP

    # Best-effort teardown: close server first (client reader sees EOF), then
    # the client, each guarded so a slow socket teardown can't hang the test.
    server.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(client.close(), timeout=2.0)


@pytest.mark.asyncio
async def test_client_headless_when_no_overlay(short_sock_dir, monkeypatch):
    sock_file = short_sock_dir / "nope.sock"
    monkeypatch.setattr(client_mod, "socket_path", lambda: sock_file)
    monkeypatch.setattr(client_mod, "spawn_overlay_candidate", lambda: None)
    monkeypatch.setattr(client_mod, "_CONNECT_ATTEMPTS", 2)
    monkeypatch.setattr(client_mod, "_CONNECT_DELAY", 0.01)

    client = client_mod.PawgressClient(on_control=lambda m: None, label="x")
    await client.connect()  # must not raise
    client.send_state(IslandState(goal="g", state=IslandStatus.WORKING))  # no-op
    await client.close()
