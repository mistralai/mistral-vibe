from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from vibe.cli.textual_ui import app as app_module
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody, _PromptSpinner
from vibe.cli.textual_ui.widgets.messages import UserMessage


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

    agent_loop = build_test_agent_loop()
    original_switch_agent = agent_loop.switch_agent
    gate = threading.Event()

    async def slow_switch_agent(agent_name: str) -> None:
        await asyncio.to_thread(gate.wait)
        await original_switch_agent(agent_name)

    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)

        with patch.object(agent_loop, "switch_agent", side_effect=slow_switch_agent):
            await pilot.press("shift+tab")
            await pilot.pause(0.05)
            await pilot.pause()

            assert body.prompt_widget is not None
            assert body.prompt_widget.display is False
            assert len(body.query(_PromptSpinner)) == 1

            gate.set()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

        assert body.switching_mode is False
        assert app.agent_loop.agent_profile.name == "plan"


@pytest.mark.asyncio
async def test_switch_failure_clears_switching_mode() -> None:
    agent_loop = build_test_agent_loop()
    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)

        with patch.object(
            agent_loop, "switch_agent", side_effect=RuntimeError("mode switch failed")
        ):
            app.action_cycle_mode()
            assert body.switching_mode is True

            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

        assert body.switching_mode is False
        assert body.prompt_widget is not None
        assert body.prompt_widget.display is True
        assert len(body.query(_PromptSpinner)) == 0


@pytest.mark.asyncio
async def test_switch_failure_rebases_next_cycle_on_active_agent() -> None:
    agent_loop = build_test_agent_loop()
    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        manager = agent_loop.agent_manager
        expected = manager.next_agent(agent_loop.agent_profile)

        with patch.object(
            agent_loop, "switch_agent", side_effect=RuntimeError("mode switch failed")
        ):
            app.action_cycle_mode()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

        app.action_cycle_mode()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        assert app.agent_loop.agent_profile.name == expected.name


@pytest.mark.asyncio
async def test_external_switch_rebases_next_cycle_on_active_agent() -> None:
    agent_loop = build_test_agent_loop()
    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        manager = agent_loop.agent_manager
        expected = manager.next_agent(agent_loop.agent_profile)

        app.action_cycle_mode()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        await agent_loop.switch_agent("default")
        app.action_cycle_mode()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        assert app.agent_loop.agent_profile.name == expected.name


@pytest.mark.asyncio
async def test_rapid_switches_land_on_latest_agent() -> None:
    agent_loop = build_test_agent_loop()
    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)

        manager = agent_loop.agent_manager
        step1 = manager.next_agent(agent_loop.agent_profile)
        step2 = manager.next_agent(step1)

        # Both presses land before the switch worker runs, so they coalesce.
        app.action_cycle_mode()
        app.action_cycle_mode()

        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        assert app.agent_loop.agent_profile.name == step2.name
        assert body.switching_mode is False


@pytest.mark.asyncio
async def test_switch_in_flight_supersedes_and_lands_on_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MODE_SWITCH_SPINNER_DELAY", 0.01)

    agent_loop = build_test_agent_loop()
    original_switch_agent = agent_loop.switch_agent
    gate = threading.Event()

    async def slow_switch_agent(agent_name: str) -> None:
        await asyncio.to_thread(gate.wait)
        await original_switch_agent(agent_name)

    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        manager = agent_loop.agent_manager
        step2 = manager.next_agent(manager.next_agent(agent_loop.agent_profile))

        with patch.object(agent_loop, "switch_agent", side_effect=slow_switch_agent):
            await pilot.press("shift+tab")
            await pilot.pause()
            # First switch is now in-flight (gated); a second press supersedes it.
            await pilot.press("shift+tab")
            await pilot.pause()

            gate.set()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

        assert app.agent_loop.agent_profile.name == step2.name


@pytest.mark.asyncio
async def test_overlapping_switches_release_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "MODE_SWITCH_SPINNER_DELAY", 0.01)

    agent_loop = build_test_agent_loop()
    original_switch_agent = agent_loop.switch_agent
    gate = threading.Event()

    async def slow_switch_agent(agent_name: str) -> None:
        await asyncio.to_thread(gate.wait)
        await original_switch_agent(agent_name)

    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)

        with patch.object(agent_loop, "switch_agent", side_effect=slow_switch_agent):
            await pilot.press("shift+tab")
            await pilot.pause()
            await pilot.press("shift+tab")
            await pilot.pause()

            assert body.switching_mode is True

            gate.set()
            await pilot.app.workers.wait_for_complete()
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

    agent_loop = build_test_agent_loop()
    original_switch_agent = agent_loop.switch_agent
    gate = threading.Event()

    async def slow_switch_agent(agent_name: str) -> None:
        await asyncio.to_thread(gate.wait)
        await original_switch_agent(agent_name)

    app = build_test_vibe_app(agent_loop=agent_loop)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        body = app.query_one(ChatInputBody)

        with patch.object(agent_loop, "switch_agent", side_effect=slow_switch_agent):
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
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

        assert body.switching_mode is False
        assert app.agent_loop.agent_profile.name == "plan"
