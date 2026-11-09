"""Real Unified Harness cleanup while the multiplex supervisor stays alive."""

from __future__ import annotations

import asyncio
import gc
from pathlib import Path
from typing import Any
import weakref

import pytest

from tests.stubs.process_services import FakeOrgConfigServer
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import (
    HarnessProcess,
    HarnessServer,
    create_harness_server,
)
from vibe.app_server.multiplex import ChannelOpen, serve_multiplexed
from vibe.app_server.server import AppServer
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair


@pytest.mark.asyncio
async def test_closed_sessions_release_their_servers_and_background_tasks(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
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

    async def factory(
        channel: JsonRpcTransport, _options: ChannelOpen | None
    ) -> HarnessServer:
        server = await create_harness_server(
            channel,
            transport_kind="stdio",
            process=HarnessProcess(services=process_services),
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
        # One pass can leave cycles that only become collectable after it.
        for _ in range(3):
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


@pytest.mark.asyncio
async def test_sessions_on_different_channels_share_one_org_config_fetch(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    # Each channel builds its own HarnessProcess, on the services the entry
    # point built once: two sessions opening at once fetch the org config once.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    org_server.serve('default_agent = "ask"\n')
    org_server.release = asyncio.Event()
    client, transport = memory_transport_pair()
    processes: list[HarnessProcess] = []

    async def factory(
        channel: JsonRpcTransport, _options: ChannelOpen | None
    ) -> HarnessServer:
        process = HarnessProcess(services=process_services)
        processes.append(process)
        return await create_harness_server(
            channel, transport_kind="stdio", process=process
        )

    serving = asyncio.create_task(serve_multiplexed(transport, factory))
    responses: dict[tuple[str, int], asyncio.Future[dict[str, Any]]] = {}

    async def route() -> None:
        async for frame in client.messages():
            message = frame["message"]
            if message is None or "method" in message:
                continue
            key = (frame["channelId"], message.get("id"))
            if key in responses:
                assert "error" not in message, message
                responses[key].set_result(message["result"])

    routing = asyncio.create_task(route())
    request_id = 0

    async def rpc(channel: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal request_id
        request_id += 1
        answer = asyncio.get_running_loop().create_future()
        responses[channel, request_id] = answer
        await client.send({
            "channelId": channel,
            "message": {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        })
        return await asyncio.wait_for(answer, 10)

    async def open_session(channel: str) -> str:
        await rpc(
            channel, "initialize", {"clientInfo": {"name": "org-test", "version": "1"}}
        )
        await client.send({
            "channelId": channel,
            "message": {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        })
        started = await rpc(
            channel, "session/start", {"agentConfig": {"cwd": str(tmp_path)}}
        )
        return started["state"]["session"]["id"]

    try:
        opening = asyncio.gather(open_session("1"), open_session("2"))
        await asyncio.sleep(0.2)
        org_server.release.set()
        sessions = await opening

        assert len({id(process) for process in processes}) == 2
        assert org_server.fetches == 1
        for channel, session in zip(("1", "2"), sessions, strict=True):
            await rpc(channel, "session/stop", {"sessionId": session})
    finally:
        await client.close()
        await serving
        routing.cancel()
