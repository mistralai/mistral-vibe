from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.models import PublicCheckpointEntry, PublicMessageEntry
from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import Role


@pytest.mark.asyncio
async def test_rewind_replaces_public_suffix_and_preserves_original_session(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="First response")],
        [mock_llm_chunk(content="Second response")],
    ])
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    session = await create_test_app_server_session(agent_loop)

    try:
        _ = [
            event
            async for event in session.act("first message", client_message_id="user-1")
        ]
        _ = [
            event
            async for event in session.act("second message", client_message_id="user-2")
        ]
        original_session_id = session.session_id

        result = await session.resources.sessions.rewind("user-2", restore_files=False)

        original_path = SessionLoader.find_session_by_id(
            original_session_id, config.session_logging
        )
        assert original_path is not None
        original_messages, _ = SessionLoader.load_session(original_path)
    finally:
        await session.close()
        await agent_loop.aclose()

    assert result.state.session.id != original_session_id
    assert session.history == result.state.history.entries
    assert [
        entry.text for entry in session.history if isinstance(entry, PublicMessageEntry)
    ] == ["first message", "First response"]
    assert isinstance(session.history[-1], PublicCheckpointEntry)
    assert session.history[-1].kind == "rewind"
    assert all(entry.session_id == result.state.session.id for entry in session.history)
    assert [
        message.content
        for message in original_messages
        if message.role is not Role.system
    ] == ["first message", "First response", "second message", "Second response"]
