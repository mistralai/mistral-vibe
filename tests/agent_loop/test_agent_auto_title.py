from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_tool import FakeTool
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import (
    BaseEvent,
    FunctionCall,
    SessionTitleUpdatedEvent,
    ToolCall,
    UserMessageEvent,
)


def _make_agent_loop(tmp_path: Path) -> AgentLoop:
    session_logging = SessionLoggingConfig(
        save_dir=str(tmp_path / "sessions"), session_prefix="session", enabled=True
    )
    config = build_test_vibe_config(session_logging=session_logging)
    backend = FakeBackend([
        [mock_llm_chunk(content="ok")],
        [mock_llm_chunk(content="ok")],
    ])
    return build_test_agent_loop(
        config=config, backend=backend, auto_title_enabled=True
    )


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
    async def test_event_when_session_logging_disabled(self, tmp_path: Path) -> None:
        config = build_test_vibe_config()
        backend = FakeBackend(mock_llm_chunk(content="ok"))
        loop = build_test_agent_loop(config=config, backend=backend)

        events = await _collect(loop, "rendered", auto_title="Pretty title")

        title_events = [e for e in events if isinstance(e, SessionTitleUpdatedEvent)]
        assert [event.title for event in title_events] == ["Pretty title"]


class TestAgentLoopIntraTurnScheduling:
    @pytest.mark.asyncio
    async def test_schedules_title_between_tool_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A single user turn that runs a tool call spans two model steps. The
        # title must be scheduled after the first step, not only once the whole
        # (possibly minutes-long) turn finishes.
        seen: list[int] = []
        original = AgentLoop._maybe_schedule_title_generation

        def spy(self: AgentLoop, *, turn_completing: bool) -> None:
            seen.append(len(self.messages))
            original(self, turn_completing=turn_completing)

        monkeypatch.setattr(AgentLoop, "_maybe_schedule_title_generation", spy)

        session_logging = SessionLoggingConfig(
            save_dir=str(tmp_path / "sessions"), session_prefix="session", enabled=True
        )
        config = build_test_vibe_config(
            session_logging=session_logging, enabled_tools=["stub_tool"]
        )
        tool_call = ToolCall(
            id="call_1",
            index=0,
            function=FunctionCall(name="stub_tool", arguments="{}"),
        )
        backend = FakeBackend([
            [mock_llm_chunk(content="calling", tool_calls=[tool_call])],
            [mock_llm_chunk(content="done")],
        ])
        loop = build_test_agent_loop(
            config=config,
            agent_name=BuiltinAgentName.AUTO_APPROVE,
            backend=backend,
            auto_title_enabled=True,
        )
        loop.tool_manager._all_tools["stub_tool"] = FakeTool

        await _collect(loop, "run it")

        assert len(seen) >= 2
        await loop.aclose()


class TestAgentLoopBackgroundTitle:
    @staticmethod
    def _patch_generator(monkeypatch: pytest.MonkeyPatch, title: str | None) -> None:
        async def fake_generate(
            messages, *, config, previous_title=None, policy=None
        ) -> str | None:
            return title

        monkeypatch.setattr(
            "vibe.core.session.title_model.generate_session_title", fake_generate
        )

    @pytest.mark.asyncio
    async def test_persists_generated_title_and_offers_it_out_of_band(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_generator(monkeypatch, "Generated title")
        loop = _make_agent_loop(tmp_path)

        await _collect(loop, "first", auto_title="Deterministic")
        assert loop._auto_title_task is not None
        await loop._auto_title_task

        assert loop.session_logger.title == "Generated title"
        # The result waits on the out-of-band queue for its single drain
        # consumer; a later turn never re-emits it into the turn stream.
        assert loop._out_of_band_events.qsize() == 1

        events = await _collect(loop, "second", auto_title=None)
        title_events = [e for e in events if isinstance(e, SessionTitleUpdatedEvent)]
        assert title_events == []
        assert loop._out_of_band_events.qsize() == 1

    @pytest.mark.asyncio
    async def test_out_of_band_events_yields_title_immediately(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_generator(monkeypatch, "Generated title")
        loop = _make_agent_loop(tmp_path)

        await _collect(loop, "first", auto_title=None)
        assert loop._auto_title_task is not None
        await loop._auto_title_task

        # The result is available out-of-band without waiting for the next turn.
        stream = loop.out_of_band_events()
        event = await asyncio.wait_for(stream.__anext__(), timeout=1)
        await stream.aclose()

        assert isinstance(event, SessionTitleUpdatedEvent)
        assert event.title == "Generated title"
        # Stamped with the originating session so a reset can drop a stale title.
        assert event.session_id == loop.session_id

    @pytest.mark.asyncio
    async def test_does_not_regenerate_manual_title(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_generator(monkeypatch, "Generated title")
        loop = _make_agent_loop(tmp_path)
        loop.session_logger._set_title("Manual title")

        await _collect(loop, "first", auto_title=None)

        assert loop._auto_title_task is None
        assert loop.session_logger.title == "Manual title"

    @pytest.mark.asyncio
    async def test_no_scheduling_when_auto_title_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Surfaces that don't opt in (the delivery layer decides) must never
        # trigger the background title LLM call; they degrade to the preview.
        self._patch_generator(monkeypatch, "Generated title")
        session_logging = SessionLoggingConfig(
            save_dir=str(tmp_path / "sessions"), session_prefix="session", enabled=True
        )
        config = build_test_vibe_config(session_logging=session_logging)
        backend = FakeBackend([[mock_llm_chunk(content="ok")]])
        loop = build_test_agent_loop(
            config=config, backend=backend, auto_title_enabled=False
        )

        await _collect(loop, "first", auto_title=None)

        assert loop._auto_title_task is None
        assert loop.session_logger.title is None

    @pytest.mark.asyncio
    async def test_env_kill_switch_disables_scheduling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_generator(monkeypatch, "Generated title")
        monkeypatch.setenv("VIBE_TEST_DISABLE_AUTO_TITLE", "1")
        loop = _make_agent_loop(tmp_path)

        await _collect(loop, "first", auto_title="Deterministic")

        assert loop._auto_title_task is None
        assert loop.session_logger.title == "Deterministic"

    @pytest.mark.asyncio
    async def test_no_task_when_generator_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_generator(monkeypatch, None)
        loop = _make_agent_loop(tmp_path)

        await _collect(loop, "first", auto_title="Deterministic")
        assert loop._auto_title_task is not None
        await loop._auto_title_task

        assert loop.session_logger.title == "Deterministic"

    @pytest.mark.asyncio
    async def test_compaction_forces_refresh_before_interval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def fake_generate(
            messages, *, config, previous_title=None, policy=None
        ) -> str:
            nonlocal calls
            calls += 1
            return f"Title {calls}"

        monkeypatch.setattr(
            "vibe.core.session.title_model.generate_session_title", fake_generate
        )
        loop = _make_agent_loop(tmp_path)

        await _collect(loop, "first", auto_title="Deterministic")
        assert loop._auto_title_task is not None
        await loop._auto_title_task
        assert calls == 1

        loop._title_cadence.mark_compaction()
        await _collect(loop, "second", auto_title=None)
        assert loop._auto_title_task is not None
        await loop._auto_title_task
        assert calls == 2

    @pytest.mark.asyncio
    async def test_retries_next_turn_when_generation_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def fake_generate(
            messages, *, config, previous_title=None, policy=None
        ) -> str | None:
            nonlocal calls
            calls += 1
            return None if calls == 1 else "Generated title"

        monkeypatch.setattr(
            "vibe.core.session.title_model.generate_session_title", fake_generate
        )
        loop = _make_agent_loop(tmp_path)

        await _collect(loop, "first", auto_title="Deterministic")
        assert loop._auto_title_task is not None
        await loop._auto_title_task
        assert calls == 1
        assert loop.session_logger.title == "Deterministic"

        # The failed attempt restored the due flags, so the next turn retries.
        await _collect(loop, "second", auto_title=None)
        assert loop._auto_title_task is not None
        await loop._auto_title_task
        assert calls == 2
        assert loop.session_logger.title == "Generated title"

    @pytest.mark.asyncio
    async def test_compaction_refresh_retries_after_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outcomes: list[str | None] = ["Title 1", None, "Title 2"]
        calls = 0

        async def fake_generate(
            messages, *, config, previous_title=None, policy=None
        ) -> str | None:
            nonlocal calls
            outcome = outcomes[calls]
            calls += 1
            return outcome

        monkeypatch.setattr(
            "vibe.core.session.title_model.generate_session_title", fake_generate
        )
        session_logging = SessionLoggingConfig(
            save_dir=str(tmp_path / "sessions"), session_prefix="session", enabled=True
        )
        config = build_test_vibe_config(session_logging=session_logging)
        backend = FakeBackend([[mock_llm_chunk(content="ok")] for _ in range(3)])
        loop = build_test_agent_loop(
            config=config, backend=backend, auto_title_enabled=True
        )

        await _collect(loop, "first", auto_title="Deterministic")
        assert loop._auto_title_task is not None
        await loop._auto_title_task
        assert loop.session_logger.title == "Title 1"

        loop._title_cadence.mark_compaction()
        await _collect(loop, "second", auto_title=None)
        assert loop._auto_title_task is not None
        await loop._auto_title_task
        # Generation failed, so the compaction refresh flag is restored.
        assert loop._title_cadence._compacted_since_gen is True
        assert loop.session_logger.title == "Title 1"

        await _collect(loop, "third", auto_title=None)
        assert loop._auto_title_task is not None
        await loop._auto_title_task
        assert loop.session_logger.title == "Title 2"


class _RecordingCadence:
    def __init__(self) -> None:
        self.periodic_calls: list[bool] = []

    def begin_if_due(self, *, periodic: bool, turn_completing: bool = False):
        self.periodic_calls.append(periodic)
        return None


class TestAgentLoopTitleTier:
    @pytest.mark.asyncio
    async def test_refreshes_periodically_on_the_cheap_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "vibe.core.agent_loop._loop.is_fast_utility_model", lambda config: True
        )
        loop = _make_agent_loop(tmp_path)
        cadence = _RecordingCadence()
        loop._title_cadence = cadence  # type: ignore[assignment]

        loop._maybe_schedule_title_generation(turn_completing=True)

        assert cadence.periodic_calls == [True]

    @pytest.mark.asyncio
    async def test_bounds_scheduling_off_the_cheap_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "vibe.core.agent_loop._loop.is_fast_utility_model", lambda config: False
        )
        loop = _make_agent_loop(tmp_path)
        cadence = _RecordingCadence()
        loop._title_cadence = cadence  # type: ignore[assignment]

        loop._maybe_schedule_title_generation(turn_completing=True)

        assert cadence.periodic_calls == [False]
