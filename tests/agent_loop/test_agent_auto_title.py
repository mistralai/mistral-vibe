from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import BaseEvent, SessionTitleUpdatedEvent, UserMessageEvent


def _make_agent_loop(tmp_path: Path) -> AgentLoop:
    session_logging = SessionLoggingConfig(
        save_dir=str(tmp_path / "sessions"), session_prefix="session", enabled=True
    )
    config = build_test_vibe_config(session_logging=session_logging)
    backend = FakeBackend([
        [mock_llm_chunk(content="ok")],
        [mock_llm_chunk(content="ok")],
    ])
    return build_test_agent_loop(config=config, backend=backend)


async def _collect(loop: AgentLoop, prompt: str, **kwargs) -> list[BaseEvent]:
    return [ev async for ev in loop.act(prompt, **kwargs)]


class TestAgentLoopAutoTitleEvent:
    @pytest.mark.asyncio
    async def test_emits_event_on_first_user_message(self, tmp_path: Path) -> None:
        loop = _make_agent_loop(tmp_path)

        events = await _collect(loop, "rendered prompt", auto_title="Pretty title")

        title_events = [e for e in events if isinstance(e, SessionTitleUpdatedEvent)]
        assert len(title_events) == 1
        assert title_events[0].title == "Pretty title"

    @pytest.mark.asyncio
    async def test_event_fires_after_user_message_event(self, tmp_path: Path) -> None:
        loop = _make_agent_loop(tmp_path)

        events = await _collect(loop, "rendered", auto_title="Pretty")

        indices = {
            type(e).__name__: i
            for i, e in enumerate(events)
            if isinstance(e, (UserMessageEvent, SessionTitleUpdatedEvent))
        }
        assert indices["UserMessageEvent"] < indices["SessionTitleUpdatedEvent"]

    @pytest.mark.asyncio
    async def test_no_event_on_second_message(self, tmp_path: Path) -> None:
        loop = _make_agent_loop(tmp_path)
        await _collect(loop, "first", auto_title="First title")

        events = await _collect(loop, "second", auto_title="Second title")

        title_events = [e for e in events if isinstance(e, SessionTitleUpdatedEvent)]
        assert title_events == []

    @pytest.mark.asyncio
    async def test_no_event_when_auto_title_is_none(self, tmp_path: Path) -> None:
        loop = _make_agent_loop(tmp_path)

        events = await _collect(loop, "rendered", auto_title=None)

        title_events = [e for e in events if isinstance(e, SessionTitleUpdatedEvent)]
        assert title_events == []

    @pytest.mark.asyncio
    async def test_no_event_when_session_logging_disabled(self, tmp_path: Path) -> None:
        config = build_test_vibe_config()
        backend = FakeBackend(mock_llm_chunk(content="ok"))
        loop = build_test_agent_loop(config=config, backend=backend)

        events = await _collect(loop, "rendered", auto_title="Pretty title")

        title_events = [e for e in events if isinstance(e, SessionTitleUpdatedEvent)]
        assert title_events == []
