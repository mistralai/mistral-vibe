from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui import app as app_module
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody, _PromptSpinner
from vibe.cli.textual_ui.widgets.messages import UserMessage


async def _wait_for_mode_switch(app: app_module.VibeApp) -> None:
    workers = [worker for worker in app.workers if worker.group == "mode_switch"]
    if workers:
        await app.workers.wait_for_complete(workers)


@pytest.mark.asyncio
async def test_submit_ignored_while_switching_mode() -> None:
    """Enter press during mode switch must not clear input or send a message."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)
        body.switching_mode = True
        await pilot.pause(0.1)

        # Type some text and press enter
        app.query_one(ChatInputContainer).value = "hello world"
        await pilot.press("enter")
        await pilot.pause(0.1)

        # Text must remain in the input
        assert app.query_one(ChatInputContainer).value == "hello world"
        # No user message should have been posted
        assert len(app.query(UserMessage)) == 0


@pytest.mark.asyncio
async def test_submit_works_after_switching_mode_ends() -> None:
    """After switching_mode is set back to False, Enter should work normally."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)

        # Enable then disable switching mode
        body.switching_mode = True
        await pilot.pause(0.1)
        body.switching_mode = False
        await pilot.pause(0.1)

        # Now submit should work
        app.query_one(ChatInputContainer).value = "hello"
        await pilot.press("enter")
        await pilot.pause(0.1)

        assert app.query_one(ChatInputContainer).value == ""


@pytest.mark.asyncio
async def test_spinner_shown_while_switching_mode() -> None:
    """Prompt widget is hidden and spinner is mounted when switching_mode is True."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)
        prompt = body.prompt_widget
        assert prompt is not None
        assert prompt.display is True
        assert len(body.query(_PromptSpinner)) == 0

        body.switching_mode = True
        await pilot.pause(0.1)

        assert prompt.display is False
        assert len(body.query(_PromptSpinner)) == 1


@pytest.mark.asyncio
async def test_spinner_removed_after_switching_mode_ends() -> None:
    """Prompt is restored and spinner removed when switching_mode becomes False."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)
        body.switching_mode = True
        await pilot.pause(0.1)
        body.switching_mode = False
        await pilot.pause(0.1)

        assert body.prompt_widget is not None
        assert body.prompt_widget.display is True
        assert len(body.query(_PromptSpinner)) == 0


@pytest.mark.asyncio
async def test_rapid_switching_mode_no_duplicate_spinners() -> None:
    """Rapidly toggling switching_mode must never produce duplicate spinners."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)

        # Rapidly toggle several times
        for _ in range(5):
            body.switching_mode = True
            body.switching_mode = True  # double set
        await pilot.pause(0.1)

        assert len(body.query(_PromptSpinner)) == 1


@pytest.mark.asyncio
async def test_shift_tab_slow_switch_shows_delayed_spinner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MODE_SWITCH_SPINNER_DELAY", 0.01)

    gate = asyncio.Event()
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)
        agents = app.app_server.resources.agents
        original_switch = agents.switch

        async def slow_switch(agent_name: str) -> None:
            await gate.wait()
            await original_switch(agent_name)

        with patch.object(agents, "switch", side_effect=slow_switch):
            await pilot.press("shift+tab")
            await pilot.pause(0.05)
            await pilot.pause()

            assert body.prompt_widget is not None
            assert body.prompt_widget.display is False
            assert len(body.query(_PromptSpinner)) == 1

            gate.set()
            await _wait_for_mode_switch(app)
            await pilot.pause()

        assert body.switching_mode is False
        assert app.app_server.resources.agents.active.name == "plan"


@pytest.mark.asyncio
async def test_switch_failure_clears_switching_mode() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)
        agents = app.app_server.resources.agents

        with patch.object(
            agents, "switch", side_effect=RuntimeError("mode switch failed")
        ):
            app.action_cycle_mode()
            assert body.switching_mode is True

            await _wait_for_mode_switch(app)
            await pilot.pause()

        assert body.switching_mode is False
        assert body.prompt_widget is not None
        assert body.prompt_widget.display is True
        assert len(body.query(_PromptSpinner)) == 0


@pytest.mark.asyncio
async def test_switch_failure_rebases_next_cycle_on_active_agent() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        agents = app.app_server.resources.agents

        expected = agents.next()

        with patch.object(
            agents, "switch", side_effect=RuntimeError("mode switch failed")
        ):
            app.action_cycle_mode()
            await _wait_for_mode_switch(app)
            await pilot.pause()

        app.action_cycle_mode()
        await _wait_for_mode_switch(app)
        await pilot.pause()

        assert app.app_server.resources.agents.active.name == expected.name


@pytest.mark.asyncio
async def test_external_switch_rebases_next_cycle_on_active_agent() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        agents = app.app_server.resources.agents

        expected = agents.next()

        app.action_cycle_mode()
        await _wait_for_mode_switch(app)
        await pilot.pause()

        await app.app_server.resources.agents.switch("default")
        app.action_cycle_mode()
        await _wait_for_mode_switch(app)
        await pilot.pause()

        assert app.app_server.resources.agents.active.name == expected.name


@pytest.mark.asyncio
async def test_rapid_switches_land_on_latest_agent() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)
        agents = app.app_server.resources.agents

        step1 = agents.next()
        step2 = agents.next(step1.name)

        # Both presses land before the switch worker runs, so they coalesce.
        app.action_cycle_mode()
        app.action_cycle_mode()

        await _wait_for_mode_switch(app)
        await pilot.pause()

        assert app.app_server.resources.agents.active.name == step2.name
        assert body.switching_mode is False


@pytest.mark.asyncio
async def test_shift_tab_switches_repeatedly_during_turn() -> None:
    backend_started = asyncio.Event()
    release_backend = asyncio.Event()

    class GatedBackend(FakeBackend):
        async def complete_streaming(self, **kwargs):
            backend_started.set()
            await release_backend.wait()
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk

    backend = GatedBackend([mock_llm_chunk(content="Done")])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        agents = app.app_server.resources.agents
        first = agents.next()
        second = agents.next(first.name)

        app.query_one(ChatInputContainer).value = "keep working"
        await pilot.press("enter")
        await asyncio.wait_for(backend_started.wait(), timeout=1)

        try:
            await pilot.press("shift+tab")
            await _wait_for_mode_switch(app)
            await pilot.pause()
            assert agents.active.name == first.name

            await pilot.press("shift+tab")
            await _wait_for_mode_switch(app)
            await pilot.pause()
            assert agents.active.name == second.name
        finally:
            release_backend.set()

        await pilot.pause(0.1)
        assert agents.active.name == second.name


@pytest.mark.asyncio
async def test_switch_in_flight_supersedes_and_lands_on_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MODE_SWITCH_SPINNER_DELAY", 0.01)

    gate = asyncio.Event()
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        agents = app.app_server.resources.agents
        original_switch = agents.switch

        step1 = agents.next()
        step2 = agents.next(step1.name)

        async def slow_switch(agent_name: str) -> None:
            await gate.wait()
            await original_switch(agent_name)

        with patch.object(agents, "switch", side_effect=slow_switch):
            await pilot.press("shift+tab")
            await pilot.pause()
            # First switch is now in-flight (gated); a second press supersedes it.
            await pilot.press("shift+tab")
            await pilot.pause()

            gate.set()
            await _wait_for_mode_switch(app)
            await pilot.pause()

        assert app.app_server.resources.agents.active.name == step2.name


@pytest.mark.asyncio
async def test_overlapping_switches_release_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MODE_SWITCH_SPINNER_DELAY", 0.01)

    gate = asyncio.Event()
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)
        agents = app.app_server.resources.agents
        original_switch = agents.switch

        async def slow_switch(agent_name: str) -> None:
            await gate.wait()
            await original_switch(agent_name)

        with patch.object(agents, "switch", side_effect=slow_switch):
            await pilot.press("shift+tab")
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()

            assert body.switching_mode is True

            gate.set()
            await _wait_for_mode_switch(app)
            await pilot.pause()

        assert body.switching_mode is False
        assert body.prompt_widget is not None
        assert body.prompt_widget.display is True
        assert len(body.query(_PromptSpinner)) == 0


@pytest.mark.asyncio
async def test_shift_tab_blocks_submit_before_spinner_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MODE_SWITCH_SPINNER_DELAY", 10)

    gate = asyncio.Event()
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)
        agents = app.app_server.resources.agents
        original_switch = agents.switch

        async def slow_switch(agent_name: str) -> None:
            await gate.wait()
            await original_switch(agent_name)

        with patch.object(agents, "switch", side_effect=slow_switch):
            app.query_one(ChatInputContainer).value = "hello while switching"
            app.action_cycle_mode()

            assert body.switching_mode is True
            assert body.prompt_widget is not None
            assert body.prompt_widget.display is True
            assert len(body.query(_PromptSpinner)) == 0

            await pilot.press("enter")
            await pilot.pause()

            assert app.query_one(ChatInputContainer).value == "hello while switching"
            assert len(app.query(UserMessage)) == 0

            gate.set()
            await _wait_for_mode_switch(app)
            await pilot.pause()

        assert body.switching_mode is False
        assert app.app_server.resources.agents.active.name == "plan"
