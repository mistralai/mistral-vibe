from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.widgets.messages import AssistantMessage
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import Role


@pytest.mark.asyncio
async def test_improve_command_builds_prompt_and_submits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    built_prompt = "Analyze my recent sessions and tell me where Vibe should improve."

    def fake_build_improve_prompt(
        session_config: SessionLoggingConfig,
        current_session_dir: Path | None,
        limit: int,
    ) -> str:
        captured["session_config"] = session_config
        captured["current_session_dir"] = current_session_dir
        captured["limit"] = limit
        return built_prompt

    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.build_improve_prompt", fake_build_improve_prompt
    )

    session_logging = SessionLoggingConfig(
        save_dir=str(tmp_path / "sessions"),
        session_prefix="test",
        enabled=True,
    )
    config = build_test_vibe_config(session_logging=session_logging)
    backend = FakeBackend(
        [[mock_llm_chunk(content="Tighten slash command defaults.")]]
    )
    agent_loop = build_test_agent_loop(config=config, backend=backend)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.press(*"/improve")
        await pilot.press("enter")
        await pilot.pause(0.5)
        assistant_messages = app.query(AssistantMessage)
        assert len(assistant_messages) == 1
        assert assistant_messages[0]._content == "Tighten slash command defaults."

    assert captured["session_config"] == session_logging
    assert captured["current_session_dir"] == agent_loop.session_logger.session_dir
    assert captured["limit"] == 50

    assert any(
        message.role == Role.user
        and message.content == built_prompt
        and message.persist_to_session_log is False
        for message in agent_loop.messages
    )
    assert any(
        message.role == Role.assistant
        and message.content == "Tighten slash command defaults."
        and message.persist_to_session_log is False
        for message in agent_loop.messages
    )
