"""Real Unified Harness cleanup while the multiplex supervisor stays alive."""

from __future__ import annotations

import asyncio
import gc
from pathlib import Path
from typing import Any
import weakref

import pytest

from vibe.app_server._runtime import HarnessServer, create_harness_server
from vibe.app_server.multiplex import serve_multiplexed
from vibe.app_server.server import AppServer
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair


@pytest.mark.asyncio
async def test_closed_sessions_release_their_servers_and_background_tasks(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("TEST_FAKE_API_KEY", "test")
    (config_dir / "config.toml").write_text("""
active_model = "fake-model"
include_project_context = false
[experiments]
enable = false
[telemetry]
enable = false
[[providers]]
name = "fake"
api_base = "http://127.0.0.1:1/v1"
api_key_env_var = "TEST_FAKE_API_KEY"
api_style = "openai"
[[models]]
name = "fake-model"
provider = "fake"
alias = "fake-model"
thinking = "off"
""")
    client, transport = memory_transport_pair()
    references: list[weakref.ReferenceType[AppServer]] = []

    async def factory(channel: JsonRpcTransport) -> HarnessServer:
        server = await create_harness_server(
            channel, transport_kind="stdio", experimental_harness=True
        )
        references.append(weakref.ref(server._server))
        return server

    serving = asyncio.create_task(serve_multiplexed(transport, factory))
    incoming = client.messages()
    request_id = 0

    async def rpc(channel: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal request_id
        request_id += 1
        await client.send({
            "channelId": channel,
            "message": {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        })
        async for frame in incoming:
            message = frame["message"]
            if message is not None and message.get("id") == request_id:
                assert "error" not in message, message
                return message["result"]
        raise AssertionError("Connection closed before responding")

    try:
        for channel in ("1", "2", "3"):
            await rpc(
                channel,
                "initialize",
                {"clientInfo": {"name": "lifecycle-test", "version": "1"}},
            )
            await client.send({
                "channelId": channel,
                "message": {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            })
            started = await rpc(
                channel, "session/start", {"agentConfig": {"cwd": str(tmp_path)}}
            )
            await rpc(
                channel,
                "session/stop",
                {"sessionId": started["state"]["session"]["id"]},
            )
            async for frame in incoming:
                if frame["message"] is None:
                    assert frame["channelId"] == channel
                    break
        await asyncio.sleep(0)
        gc.collect()
        assert not serving.done()
        assert all(reference() is None for reference in references)
        assert not any(
            task.get_name() == "vibe-unified-scheduled-loops"
            for task in asyncio.all_tasks()
        )
    finally:
        await client.close()
        await serving
