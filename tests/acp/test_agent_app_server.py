from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket

from acp.agent.connection import AgentSideConnection
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import RETRYING_EXT_METHOD, VibeAcpAgent
from vibe.acp.exceptions import ConfigurationError, UnauthenticatedError
from vibe.app_server.local import LocalHarnessHost, LocalHarnessOptions
from vibe.app_server.protocol import (
    AppServerResponseError,
    InvalidParamsData,
    InvalidParamsIssue,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.session import AppServerSession


@pytest.mark.asyncio
async def test_new_session_without_a_key_is_unauthenticated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: An ACP agent over a real backend, for a user with no key.
    *Do*: Open a session.
    *Assert*: ``UnauthenticatedError`` naming the provider.

    The code is what an editor reads to offer a sign-in; the
    ``ConfigurationError`` this used to be sends the user to edit
    ``config.toml`` instead. The Unified backend must answer
    ``UNAUTHORIZED`` with a provider.
    """
    # Prepare
    monkeypatch.delenv("MISTRAL_API_KEY")
    harness_host = LocalHarnessHost()

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        return await harness_host.start(options)

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)

    try:
        # Do
        with pytest.raises(UnauthenticatedError) as exc_info:
            await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    finally:
        await agent.close()
        await harness_host.close()

    # Assert
    assert "mistral" in str(exc_info.value)


@pytest.mark.asyncio
async def test_new_session_error_surfaces_invalid_params_detail(tmp_path: Path) -> None:
    """*Prepare*: An ACP agent whose app server rejects the session with a
    field-level ``INVALID_PARAMS`` issue. *Do*: Open a session. *Assert*: The
    ``ConfigurationError`` carries the rendered detail, not just the protocol
    code name, so an editor shows what actually failed.
    """
    issue = InvalidParamsIssue(
        path=["message", "content", 1, "resource", "resource"],
        message="Unable to extract tag using discriminator 'kind'",
    )

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        raise AppServerResponseError(
            ProtocolError(
                code=ProtocolErrorCode.INVALID_PARAMS,
                message="Invalid request parameters",
                data=InvalidParamsData(error_count=1, issues=[issue]).model_dump(
                    mode="json"
                ),
            )
        )

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)

    try:
        # Do
        with pytest.raises(ConfigurationError) as exc_info:
            await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    finally:
        await agent.close()

    # Assert
    assert "Invalid request parameters" in str(exc_info.value)
    assert "Unable to extract tag using discriminator 'kind'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_retry_notification_reaches_the_wire_underscore_prefixed() -> None:
    """`ext_notification` prefixes extension methods, so callers pass the bare name.

    The VS Code client subscribes to the prefixed `_session/retrying`; passing the
    prefix here too would emit `__session/retrying` and the retry would never
    reach the extension. FakeClient records the call argument, not the wire, so
    only this test covers the gap.
    """
    peer_socket, agent_socket = socket.socketpair()
    peer_reader, peer_writer = await asyncio.open_connection(sock=peer_socket)
    agent_reader, agent_writer = await asyncio.open_connection(sock=agent_socket)
    connection = AgentSideConnection(
        lambda _client: VibeAcpAgent(), agent_writer, agent_reader, listening=False
    )
    try:
        await connection.ext_notification(
            RETRYING_EXT_METHOD,
            {
                "sessionId": "session-1",
                "category": "rate_limited",
                "detail": "HTTP 429",
            },
        )
        line = await asyncio.wait_for(peer_reader.readline(), timeout=5)

        assert json.loads(line)["method"] == "_session/retrying"
    finally:
        agent_writer.close()
        peer_writer.close()


def test_acp_runtime_adapter_has_no_direct_core_dependency() -> None:
    root = Path(__file__).parents[2] / "vibe" / "acp"
    runtime_files = [
        root / "agent.py",
        root / "session.py",
        root / "session_updates.py",
    ]
    contents = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)

    assert "AgentLoop" not in contents
    assert ".agent_loop" not in contents
    assert "vibe.acp.tools" not in contents
