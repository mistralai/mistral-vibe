from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import tomllib
from typing import Any

import pytest
import pytest_asyncio

from vibe.app_server.stdio import serve_stdio
from vibe.app_server.transport import (
    JsonRpcTransport,
    StdioJsonRpcTransport,
    memory_transport_pair,
)
from vibe.core.trusted_folders import trusted_folders_manager


class MultiplexClient:
    def __init__(self, transport: JsonRpcTransport) -> None:
        self.transport = transport
        self.incoming = transport.messages()
        self.next_id = 0

    async def request(
        self, channel: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        await self.transport.send({
            "channelId": channel,
            "message": {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        })
        async for frame in self.incoming:
            message = frame["message"]
            if message is not None and message.get("id") == request_id:
                assert frame["channelId"] == channel
                assert "error" not in message, message
                return message["result"]
        raise AssertionError("Server closed before responding")

    async def initialize(self, channel: str) -> None:
        await self.request(
            channel,
            "initialize",
            {"clientInfo": {"name": "trust-test", "version": "1"}},
        )
        await self.transport.send({
            "channelId": channel,
            "message": {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        })

    async def start(self, channel: str, cwd: Path, *, trust: bool = False) -> str:
        await self.initialize(channel)
        result = await self.request(
            channel,
            "session/start",
            {"agentConfig": {"cwd": str(cwd), "trustWorkspace": trust}},
        )
        return result["state"]["session"]["id"]


@pytest_asyncio.fixture
async def client(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[MultiplexClient]:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("TEST_FAKE_API_KEY", "test")
    (config_dir / "config.toml").write_text("""
active_model = "fake-model"
theme = "dark"
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
    peer, transport = memory_transport_pair()
    monkeypatch.setattr(
        StdioJsonRpcTransport, "from_standard_streams", lambda: transport
    )
    serving = asyncio.create_task(
        serve_stdio(experimental_harness=True, multiplex=True)
    )
    try:
        yield MultiplexClient(peer)
    finally:
        await peer.close()
        await serving


@pytest.mark.asyncio
@pytest.mark.parametrize("peer_relative_path", [".", "packages/foo"])
async def test_temporary_trust_cannot_reach_other_channels(
    client: MultiplexClient, tmp_path: Path, peer_relative_path: str
) -> None:
    repo = tmp_path / "repo"
    peer_cwd = repo / peer_relative_path
    peer_cwd.mkdir(parents=True)
    (repo / ".vibe").mkdir()
    (repo / ".vibe" / "config.toml").write_text('theme = "light"\n')

    existing_peer = await client.start("1", peer_cwd)
    trusted = await client.start("2", repo, trust=True)
    config = await client.request("2", "config/read", {"sessionId": trusted})
    assert config["config"]["theme"] == "light"

    status = await client.request("1", "workspace/trust/status", {"cwd": str(peer_cwd)})
    assert status["status"] == "untrusted"
    new_peer = await client.start("3", peer_cwd)
    for channel, session_id in (("1", existing_peer), ("3", new_peer)):
        await client.request(channel, "config/reload", {"sessionId": session_id})
        config = await client.request(channel, "config/read", {"sessionId": session_id})
        assert config["config"]["theme"] == "dark"

    await client.request("2", "session/stop", {"sessionId": trusted})
    replacement = await client.start("4", repo)
    config = await client.request("4", "config/read", {"sessionId": replacement})
    assert config["config"]["theme"] == "dark"
    assert trusted_folders_manager.is_trusted(repo) is None


@pytest.mark.asyncio
async def test_channels_share_persistent_decisions_without_losing_updates(
    client: MultiplexClient, tmp_path: Path, config_dir: Path
) -> None:
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
        (root / "AGENTS.md").write_text("Project instructions")
    for channel in ("1", "2", "3"):
        await client.initialize(channel)

    for channel, root in zip(("1", "2"), roots, strict=True):
        result = await client.request(
            channel,
            "workspace/trust/decision",
            {"cwd": str(root), "decision": "trust_cwd"},
        )
        assert result["status"] == "trusted"

    for channel in ("1", "2", "3"):
        for root in roots:
            status = await client.request(
                channel, "workspace/trust/status", {"cwd": str(root)}
            )
            assert status["status"] == "trusted"

    stored = tomllib.loads((config_dir / "trusted_folders.toml").read_text())
    assert set(stored["trusted"]) == {str(root.resolve()) for root in roots}

    trusted_folders_manager.add_untrusted(roots[0])
    status = await client.request("2", "workspace/trust/status", {"cwd": str(roots[0])})
    assert status["status"] == "untrusted"


@pytest.mark.asyncio
async def test_channel_does_not_inherit_ambient_temporary_trust(
    client: MultiplexClient, tmp_path: Path
) -> None:
    trusted_folders_manager.trust_for_session(tmp_path)
    await client.initialize("1")
    status = await client.request("1", "workspace/trust/status", {"cwd": str(tmp_path)})
    assert status["status"] == "untrusted"
