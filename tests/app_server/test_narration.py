from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_backend import FakeBackend
import vibe.app_server._narration as narration_module


@pytest.mark.asyncio
async def test_narration_summary_uses_server_owned_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend(mock_llm_chunk(content="Concise summary"))
    monkeypatch.setattr(narration_module, "create_backend", lambda **_kwargs: backend)
    session = await create_test_app_server_session(build_test_agent_loop())

    try:
        summary = await session.resources.narration.summarize(
            user_message="Fix the bug",
            assistant_text="Changed the parser",
            error=None,
            message_id="message-1",
        )
    finally:
        await session.close()

    assert summary == "Concise summary"
    assert len(backend.requests_messages) == 1
    assert "Fix the bug" in (backend.requests_messages[0][1].content or "")
    assert "Changed the parser" in (backend.requests_messages[0][1].content or "")
    assert backend.requests_metadata[0] is not None
    assert backend.requests_metadata[0]["message_id"] == "message-1"
    assert backend.requests_metadata[0]["call_type"] == "secondary_call"


@pytest.mark.asyncio
async def test_narration_failure_returns_no_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend(exception_to_raise=RuntimeError("failed"))
    monkeypatch.setattr(narration_module, "create_backend", lambda **_kwargs: backend)
    session = await create_test_app_server_session(build_test_agent_loop())

    try:
        summary = await session.resources.narration.summarize(
            user_message="Fix the bug",
            assistant_text="",
            error="Turn failed",
            message_id=None,
        )
    finally:
        await session.close()

    assert summary is None
