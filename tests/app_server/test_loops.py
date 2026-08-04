from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import create_test_app_server_session
from vibe.app_server.protocol import AppServerResponseError, ProtocolErrorCode
from vibe.core.config import SessionLoggingConfig


@pytest.mark.asyncio
async def test_loop_mutations_use_typed_resource_methods() -> None:
    session = await create_test_app_server_session(build_test_agent_loop())
    try:
        assert await session.resources.loops.list() == []

        first = await session.resources.loops.create("30s", "first prompt")
        second = await session.resources.loops.create("1m", "second prompt")
        assert await session.resources.loops.list() == [first, second]

        deleted = await session.resources.loops.delete(first.id)
        assert deleted == first
        assert await session.resources.loops.list() == [second]

        assert await session.resources.loops.clear() == 1
        assert await session.resources.loops.list() == []
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_loop_validation_is_a_typed_protocol_error() -> None:
    session = await create_test_app_server_session(build_test_agent_loop())
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await session.resources.loops.create("invalid", "prompt")
    finally:
        await session.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "Invalid interval" in exc_info.value.error.message


@pytest.mark.asyncio
async def test_clear_history_preserves_scheduled_loops(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(config=config)
    session = await create_test_app_server_session(agent_loop)
    try:
        loop = await session.resources.loops.create("30s", "keep running")

        await session.clear_history()

        assert await session.resources.loops.list() == [loop]
        metadata = agent_loop.session_logger.session_metadata
        assert metadata is not None
        assert [scheduled.id for scheduled in metadata.loops] == [loop.id]
    finally:
        await session.close()
