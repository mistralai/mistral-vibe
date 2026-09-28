from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import (
    attach_test_app_server_session,
    create_test_app_server_session,
    start_test_app_server,
)
from vibe.app_server.protocol import (
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
)
from vibe.core.feedback import _CACHE_SECTION, _LAST_SHOWN_KEY
from vibe.core.types import LLMMessage, Role
from vibe.feedback import FEEDBACK_SNOOZED_COOLDOWN_SECONDS


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


@pytest.mark.asyncio
async def test_feedback_response_includes_server_owned_snooze_duration() -> None:
    agent_loop = build_test_agent_loop()
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)

    try:
        response = FeedbackShouldShowResponse.model_validate(
            await client.request(
                "feedback/shouldShow",
                FeedbackShouldShowParams(
                    session_id=session.session_id, pending_user_messages=1
                ),
            )
        )

        assert response.snooze_duration_seconds == FEEDBACK_SNOOZED_COOLDOWN_SECONDS
    finally:
        await session.close()
        await agent_loop.aclose()
