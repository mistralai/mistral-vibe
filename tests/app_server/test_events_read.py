"""Tests for the events/read procedure."""

from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import attach_test_app_server_session, start_test_app_server
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.protocol import EventBatch, EventsReadParams


@pytest.mark.asyncio
async def test_events_read_returns_empty_batch() -> None:
    backend = FakeBackend([mock_llm_chunk(content="hi")])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)
    try:
        # Drive a turn so events are emitted over the push stream; events/read
        # intentionally remains an empty placeholder until replay is backed.
        _ = [event async for event in session.act("hello", client_message_id="u1")]
        result = await client.request(
            "events/read", EventsReadParams().model_dump(mode="json", by_alias=True)
        )
    finally:
        await session.close()
        await agent_loop.aclose()

    batch = EventBatch.model_validate(result)
    assert batch.type == "events"
    assert batch.events == []
