from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import create_test_app_server_session
from vibe.core.feedback import _CACHE_SECTION, _LAST_SHOWN_KEY
from vibe.core.types import LLMMessage, Role


@pytest.mark.asyncio
async def test_feedback_resource_uses_server_owned_history_and_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.core.feedback.FEEDBACK_PROBABILITY", 1.0)
    monkeypatch.setattr("vibe.core.feedback.random.random", lambda: 0.0)
    agent_loop = build_test_agent_loop()
    monkeypatch.setattr(agent_loop.telemetry_client, "is_active", lambda: True)
    session = await create_test_app_server_session(agent_loop)
    agent_loop.messages.reset([
        LLMMessage(role=Role.user, content="one"),
        LLMMessage(role=Role.user, content="two"),
    ])

    try:
        assert await session.resources.feedback.should_show(pending_user_messages=1)

        await session.resources.feedback.record("asked")

        assert not await session.resources.feedback.should_show(pending_user_messages=1)
        assert _LAST_SHOWN_KEY in agent_loop.cache_store.read_section(_CACHE_SECTION)
    finally:
        await session.close()
        await agent_loop.aclose()
