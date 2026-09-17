from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from acp.schema import (
    AgentMessageChunk,
    SessionInfoUpdate,
    TextContentBlock,
    UsageUpdate,
    UserMessageChunk,
)
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import start_test_app_server
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import SessionStarter, VibeAcpAgent
from vibe.acp.exceptions import InvalidRequestError
from vibe.app_server.local import LocalHarnessOptions
from vibe.app_server.models import IdleSessionStatus, PublicMessageEntry, PublicSession
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.session import AppServerSession
from vibe.core.config import ModelConfig


async def _new_session(agent: VibeAcpAgent) -> str:
    response = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    return response.session_id


def _client(agent: VibeAcpAgent) -> FakeClient:
    assert isinstance(agent.client, FakeClient)
    return agent.client


@pytest.mark.asyncio
async def test_session_lifecycle_and_mode_changes_use_app_server_resources(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    session_id = await _new_session(acp_agent_loop)

    response = await acp_agent_loop.set_session_mode(session_id, "plan")

    assert response is not None
    assert (
        acp_agent_loop.sessions[session_id].app_server.resources.agents.active.name
        == "plan"
    )
    assert await acp_agent_loop.set_session_mode(session_id, "missing") is None
    assert await acp_agent_loop.set_session_mode(session_id, "explore") is None

    await acp_agent_loop.close_session(session_id)
    assert session_id not in acp_agent_loop.sessions


@pytest.mark.asyncio
async def test_close_session_remains_retryable_after_cleanup_failure(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _new_session(acp_agent_loop)
    session = acp_agent_loop.sessions[session_id]
    original_close = session.close
    attempts = 0

    async def fail_once() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("cleanup failed")
        await original_close()

    monkeypatch.setattr(session, "close", fail_once)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await acp_agent_loop.close_session(session_id)

    assert acp_agent_loop.sessions[session_id] is session

    await acp_agent_loop.close_session(session_id)

    assert session_id not in acp_agent_loop.sessions


@pytest.mark.asyncio
async def test_delete_session_remains_retryable_after_cleanup_failure(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _new_session(acp_agent_loop)
    session = acp_agent_loop.sessions[session_id]
    original_close = session.close
    attempts = 0

    async def fail_once() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("cleanup failed")
        await original_close()

    monkeypatch.setattr(session, "close", fail_once)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await acp_agent_loop.ext_method("session/delete", {"sessionId": session_id})

    assert acp_agent_loop.sessions[session_id] is session

    await acp_agent_loop.ext_method("session/delete", {"sessionId": session_id})

    assert attempts == 2
    assert session_id not in acp_agent_loop.sessions


@pytest.mark.asyncio
async def test_delete_session_uses_same_session_id_after_compaction(
    acp_agent_with_session_config: tuple[VibeAcpAgent, FakeClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = acp_agent_with_session_config
    created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    await agent.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="Hello")],
    )
    session = agent.sessions[created.session_id]

    await session.app_server.compact()

    session_id = session.app_server.session_id
    assert session_id == created.session_id
    assert session.app_server.resources.runtime.session_log.persisted
    host = Mock(delete_session=AsyncMock())
    monkeypatch.setattr(agent, "_host_resources", AsyncMock(return_value=host))

    await agent.ext_method("session/delete", {"sessionId": created.session_id})

    assert created.session_id not in agent.sessions
    host.delete_session.assert_awaited_once_with(session_id)


@pytest.mark.asyncio
async def test_config_options_delegate_to_typed_app_server_resources(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    session_id = await _new_session(acp_agent_loop)
    session = acp_agent_loop.sessions[session_id]
    active_model = session.app_server.resources.config.current.active_model.alias

    mode = await acp_agent_loop.set_config_option("mode", session_id, "plan")
    model = await acp_agent_loop.set_config_option("model", session_id, active_model)
    thinking = await acp_agent_loop.set_config_option("thinking", session_id, "low")
    max_turns = await acp_agent_loop.set_config_option("max_turns", session_id, "3")
    max_tokens = await acp_agent_loop.set_config_option(
        "max_tokens", session_id, "2000"
    )

    assert all(
        response is not None
        for response in (mode, model, thinking, max_turns, max_tokens)
    )
    with pytest.raises(InvalidRequestError):
        await acp_agent_loop.set_config_option("unknown", session_id, "x")


def _acp_agent_with_allowed_models() -> VibeAcpAgent:
    config = build_test_vibe_config(
        active_model="allowed",
        allowed_models=["allowed"],
        models=[
            ModelConfig(name="allowed", provider="mistral", alias="allowed"),
            ModelConfig(name="blocked", provider="mistral", alias="blocked"),
        ],
    )

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        loop = build_test_agent_loop(
            config=config, backend=FakeBackend(), enable_streaming=True
        )
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
        )

    starter: SessionStarter = start_session
    agent = VibeAcpAgent(session_starter=starter)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    return agent


@pytest.mark.asyncio
async def test_set_config_option_rejects_model_excluded_by_allowed_models() -> None:
    agent = _acp_agent_with_allowed_models()
    session_id = await _new_session(agent)

    with pytest.raises(InvalidRequestError):
        await agent.set_config_option("model", session_id, "blocked")

    allowed = await agent.set_config_option("model", session_id, "allowed")
    assert allowed is not None


@pytest.mark.asyncio
async def test_set_config_option_raises_instead_of_reporting_an_empty_config(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _new_session(acp_agent_loop)
    session = acp_agent_loop.sessions[session_id]
    active_model = session.app_server.resources.config.current.active_model.alias

    async def busy(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AppServerResponseError(
            ProtocolError(
                code=ProtocolErrorCode.INTERNAL_ERROR,
                message="Session is busy running prompt 1",
            )
        )

    monkeypatch.setattr(session.app_server.resources.config, "update", busy)

    with pytest.raises(InvalidRequestError) as excinfo:
        await acp_agent_loop.set_config_option("model", session_id, active_model)

    assert "busy" in str(excinfo.value)


@pytest.mark.asyncio
async def test_prompt_reports_public_usage_and_stable_message_ids(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    session_id = await _new_session(acp_agent_loop)
    client = _client(acp_agent_loop)
    client._session_updates.clear()

    response = await acp_agent_loop.prompt(
        session_id=session_id, prompt=[TextContentBlock(type="text", text="Hello")]
    )

    def usage_updates() -> list[UsageUpdate]:
        return [
            notification.update
            for notification in client._session_updates
            if isinstance(notification.update, UsageUpdate)
        ]

    for _ in range(50):
        await asyncio.sleep(0)
        if len(usage_updates()) >= 2:
            break

    assert response.usage is not None
    assert response.usage.total_tokens == 2
    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, UserMessageChunk | AgentMessageChunk)
    ]
    assert [message.content.text for message in messages] == ["Hello", "Hi"]
    assert all(message.message_id for message in messages)
    usage = usage_updates()
    assert len(usage) >= 2
    assert usage[-1].used == 2


@pytest.mark.asyncio
async def test_fork_returns_session_state_without_replaying_history(
    acp_agent_with_session_config: tuple[VibeAcpAgent, FakeClient],
) -> None:
    agent, client = acp_agent_with_session_config
    try:
        source_id = await _new_session(agent)
        await agent.prompt(
            session_id=source_id, prompt=[TextContentBlock(type="text", text="Hello")]
        )
        client._session_updates.clear()

        response = await agent.fork_session(
            source_id, cwd=str(Path.cwd()), mcp_servers=[]
        )
        await asyncio.sleep(0)

        child = agent.sessions[response.session_id]
        assert any(
            isinstance(entry, PublicMessageEntry)
            for entry in child.app_server.state.history or []
        )
        assert response.modes is not None
        assert response.config_options is not None
        assert not any(
            isinstance(notification.update, UserMessageChunk | AgentMessageChunk)
            for notification in client._session_updates
        )
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_fork_session_sends_usage_update(
    acp_agent_with_session_config: tuple[VibeAcpAgent, FakeClient],
) -> None:
    agent, client = acp_agent_with_session_config
    try:
        source_id = await _new_session(agent)
        await agent.prompt(
            session_id=source_id, prompt=[TextContentBlock(type="text", text="Hello")]
        )
        client._session_updates.clear()

        await agent.fork_session(source_id, cwd=str(Path.cwd()), mcp_servers=[])
        await asyncio.sleep(0)

        usage_updates = [
            notification.update
            for notification in client._session_updates
            if isinstance(notification.update, UsageUpdate)
        ]
        assert len(usage_updates) >= 1
        assert usage_updates[-1].size > 0
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_resume_session_sends_usage_update(
    acp_agent_with_session_config: tuple[VibeAcpAgent, FakeClient],
    temp_session_dir: Path,
    create_test_session,
) -> None:
    agent, client = acp_agent_with_session_config
    session_id = "resumable-with-stats"
    create_test_session(
        temp_session_dir,
        session_id,
        str(Path.cwd()),
        messages=[{"role": "user", "content": "Hello"}],
    )
    client._session_updates.clear()

    await agent.resume_session(
        session_id=session_id, cwd=str(Path.cwd()), mcp_servers=[]
    )
    await asyncio.sleep(0)

    usage_updates = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, UsageUpdate)
    ]
    assert len(usage_updates) >= 1
    await agent.close()


@pytest.mark.asyncio
async def test_load_session_replays_history_beyond_snapshot_page(
    acp_agent_with_session_config: tuple[VibeAcpAgent, FakeClient],
    temp_session_dir: Path,
    create_test_session,
) -> None:
    agent, client = acp_agent_with_session_config
    session_id = "long-history-session"
    messages = [{"role": "user", "content": f"message {index}"} for index in range(205)]
    create_test_session(
        temp_session_dir, session_id, str(Path.cwd()), messages=messages
    )
    client._session_updates.clear()

    await agent.load_session(cwd=str(Path.cwd()), session_id=session_id, mcp_servers=[])

    replayed = [
        notification.update.content.text
        for notification in client._session_updates
        if isinstance(notification.update, UserMessageChunk)
    ]
    assert replayed == [f"message {index}" for index in range(205)]


@pytest.mark.asyncio
async def test_title_and_session_listing_cross_the_app_server_boundary(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    session_id = await _new_session(acp_agent_loop)

    title = await acp_agent_loop.ext_method(
        "session/set_title", {"sessionId": session_id, "title": "Reviewed"}
    )
    listed = await acp_agent_loop.list_sessions(cwd=str(Path.cwd()))

    assert title == {}
    assert (
        acp_agent_loop.sessions[session_id].app_server.state.session.title == "Reviewed"
    )
    assert listed.sessions == []
    updates = [
        notification.update
        for notification in _client(acp_agent_loop)._session_updates
        if isinstance(notification.update, SessionInfoUpdate)
    ]
    assert updates[-1].title == "Reviewed"


@pytest.mark.asyncio
async def test_list_sessions_falls_back_to_preview_when_no_title(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An LLM title is only generated after a few turns; until then the ACP list
    # must surface the first-message preview so entries stay identifiable.
    items = [
        PublicSession(
            id="untitled-session",
            title=None,
            preview="MARKER-ONE first message",
            status=IdleSessionStatus(),
            created_at=0,
            updated_at=0,
            cwd=str(Path.cwd()),
        ),
        PublicSession(
            id="titled-session",
            title="Generated title",
            preview="MARKER-TWO first message",
            status=IdleSessionStatus(),
            created_at=0,
            updated_at=0,
            cwd=str(Path.cwd()),
        ),
    ]
    resources = Mock()
    resources.list_sessions = AsyncMock(return_value=items)
    monkeypatch.setattr(
        acp_agent_loop, "_host_resources", AsyncMock(return_value=resources)
    )

    listed = await acp_agent_loop.list_sessions(cwd=str(Path.cwd()))

    titles = {session.session_id: session.title for session in listed.sessions}
    assert titles["untitled-session"] == "MARKER-ONE first message"
    assert titles["titled-session"] == "Generated title"
