from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_backend import FakeBackend
from vibe.core.config import SessionLoggingConfig


@pytest.mark.asyncio
async def test_clear_replacement_becomes_resumable_after_its_first_turn(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(
        config=config,
        backend=FakeBackend([
            [mock_llm_chunk(content="Before")],
            [mock_llm_chunk(content="After")],
        ]),
        enable_streaming=True,
    )
    session = await create_test_app_server_session(agent_loop)

    try:
        _ = [event async for event in session.act("first")]
        assert session.exit_summary().session_id == session.session_id

        await session.clear_history()
        replacement_session_id = session.session_id
        assert session.exit_summary().session_id is None

        _ = [event async for event in session.act("second")]
        assert session.exit_summary().session_id == replacement_session_id
    finally:
        await session.close()
