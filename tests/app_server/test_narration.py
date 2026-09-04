from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_backend import FakeBackend
import vibe.app_server._narration as narration_module
from vibe.core.config.layers.overrides import OverridesLayer


@pytest.mark.asyncio
async def test_narration_summary_uses_current_session_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend(mock_llm_chunk(content="Concise summary"))
    backend_config: dict[str, object] = {}

    def create_backend(**kwargs: object) -> FakeBackend:
        backend_config.update(kwargs)
        return backend

    monkeypatch.setattr(narration_module, "create_backend", create_backend)
    agent_loop = build_test_agent_loop()
    monkeypatch.setattr(agent_loop, "start_initialize_experiments", lambda **_: None)
    session = await create_test_app_server_session(agent_loop)
    agent_loop.set_user_plan("Pro")
    assert not await agent_loop.config_orchestrator.set_field(
        "/api_timeout", 42.0, target_layer=OverridesLayer.NAME
    )

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
    assert backend.requests_metadata[0]["user_plan"] == "Pro"
    assert backend_config["timeout"] == 42.0


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
