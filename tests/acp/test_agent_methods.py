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

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.app_server.models import PublicMessageEntry


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
async def test_delete_session_uses_replacement_session_id_after_compaction(
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

    replacement_id = session.app_server.session_id
    assert replacement_id != created.session_id
    assert session.app_server.resources.runtime.session_log.persisted
    host = Mock(delete_session=AsyncMock())
    monkeypatch.setattr(agent, "_host_resources", AsyncMock(return_value=host))

    await agent.ext_method("session/delete", {"sessionId": created.session_id})

    assert created.session_id not in agent.sessions
    host.delete_session.assert_awaited_once_with(replacement_id)


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
    assert await acp_agent_loop.set_config_option("unknown", session_id, "x") is None


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
    await asyncio.sleep(0)

    assert response.usage is not None
    assert response.usage.total_tokens == 2
    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, UserMessageChunk | AgentMessageChunk)
    ]
    assert [message.content.text for message in messages] == ["Hello", "Hi"]
    assert all(message.message_id for message in messages)
    usage = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, UsageUpdate)
    ]
    assert len(usage) == 1


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
            for entry in child.app_server.state.history.entries
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
