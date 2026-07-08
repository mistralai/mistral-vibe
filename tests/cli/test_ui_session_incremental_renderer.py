from __future__ import annotations

import asyncio
import time
from unittest.mock import Mock

import pytest
from textual.widgets import Button

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import build_test_agent_loop
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.app import ChatScroll, VibeApp
from vibe.cli.textual_ui.widgets.load_more import (
    HistoryLoadMoreMessage,
    HistoryLoadMoreRequested,
)
from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.cli.textual_ui.windowing import (
    HISTORY_RESUME_TAIL_MESSAGES,
    LOAD_MORE_BATCH_SIZE,
)
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import LLMMessage, Role


@pytest.fixture
def vibe_config() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )


def _pro_plan_gateway() -> FakeWhoAmIGateway:
    return FakeWhoAmIGateway(
        response=WhoAmIResponse(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=False,
        )
    )


async def _wait_until(pause, predicate, timeout: float = 2.0) -> None:
    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        if predicate():
            return
        await pause(0.02)
    raise AssertionError("Condition was not met within the timeout")


async def _wait_for_load_more(app: VibeApp, pause) -> None:
    await _wait_until(
        pause, lambda: len(app.query(HistoryLoadMoreMessage)) == 1, timeout=5.0
    )


def _load_more_remaining(app: VibeApp) -> int:
    label = app.query_one(HistoryLoadMoreMessage).query_one(Button).label
    text = str(label)
    _, _, remainder = text.rpartition("(")
    return int(remainder.rstrip(")"))


@pytest.mark.asyncio
async def test_ui_mount_defers_history_resume(
    vibe_config: VibeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())
    history_started = asyncio.Event()
    history_release = asyncio.Event()
    restore_from_session = Mock()
    loop_start = Mock()
    initialize_experiments = Mock()

    async def resume_history() -> None:
        history_started.set()
        await history_release.wait()

    monkeypatch.setattr(app, "_resume_history_from_messages", resume_history)
    monkeypatch.setattr(app._loop_runner, "restore_from_session", restore_from_session)
    monkeypatch.setattr(app._loop_runner, "start", loop_start)
    monkeypatch.setattr(
        agent_loop, "start_initialize_experiments", initialize_experiments
    )

    async with asyncio.timeout(5):
        async with app.run_test() as pilot:
            await _wait_until(pilot.pause, history_started.is_set, timeout=2.0)

            app.query_one(ChatScroll)
            restore_from_session.assert_not_called()
            loop_start.assert_not_called()
            initialize_experiments.assert_not_called()

            history_release.set()
            await _wait_until(
                pilot.pause,
                lambda: (
                    restore_from_session.call_count == 1
                    and loop_start.call_count == 1
                    and initialize_experiments.call_count == 1
                ),
                timeout=2.0,
            )


@pytest.mark.asyncio
async def test_ui_session_incremental_loader_shows_tail_and_load_more(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}") for idx in range(66)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: len(app.query(UserMessage)) == HISTORY_RESUME_TAIL_MESSAGES,
            timeout=5.0,
        )
        await _wait_for_load_more(app, pilot.pause)

        assert len(app.query(UserMessage)) == HISTORY_RESUME_TAIL_MESSAGES
        load_more = app.query_one(HistoryLoadMoreMessage)
        label = load_more.query_one(Button).label
        assert "(" in str(label)


@pytest.mark.asyncio
async def test_ui_session_incremental_loader_load_more_shows_remaining_count(
    vibe_config: VibeConfig,
) -> None:
    total_messages = 31
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}")
        for idx in range(total_messages)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: len(app.query(UserMessage)) == HISTORY_RESUME_TAIL_MESSAGES,
            timeout=5.0,
        )
        await _wait_for_load_more(app, pilot.pause)

        initial_remaining = total_messages - HISTORY_RESUME_TAIL_MESSAGES
        assert _load_more_remaining(app) == initial_remaining

        app.post_message(HistoryLoadMoreRequested())
        expected_remaining = initial_remaining - LOAD_MORE_BATCH_SIZE
        await _wait_until(
            pilot.pause,
            lambda: _load_more_remaining(app) == expected_remaining,
            timeout=5.0,
        )


@pytest.mark.asyncio
async def test_ui_session_incremental_loader_load_more_batches_until_done(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}") for idx in range(31)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test() as pilot:
        await _wait_until(
            pilot.pause,
            lambda: len(app.query(UserMessage)) == HISTORY_RESUME_TAIL_MESSAGES,
            timeout=5.0,
        )
        await _wait_for_load_more(app, pilot.pause)

        total_messages = 31
        while len(app.query(HistoryLoadMoreMessage)) == 1:
            current_count = len(app.query(UserMessage))
            app.post_message(HistoryLoadMoreRequested())
            await _wait_until(
                pilot.pause,
                lambda current_count=current_count: (
                    len(app.query(UserMessage)) > current_count
                ),
            )

        await _wait_until(
            pilot.pause, lambda: len(app.query(UserMessage)) == total_messages
        )


@pytest.mark.asyncio
async def test_ui_session_incremental_loader_keeps_top_alignment_when_not_scrollable(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}")
        for idx in range(HISTORY_RESUME_TAIL_MESSAGES + 1)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    # Each UserMessage renders as ~3 rows (top margin + content + separator);
    # add chrome (input box, banner, status) so all messages fit without scrolling.
    user_message_rows = 3
    chrome_rows = 40
    viewport_height = (
        HISTORY_RESUME_TAIL_MESSAGES + 1
    ) * user_message_rows + chrome_rows

    async with app.run_test(size=(120, viewport_height)) as pilot:
        await _wait_for_load_more(app, pilot.pause)
        chat = app.query_one("#chat", ChatScroll)
        assert chat.max_scroll_y == 0
        assert chat.scroll_y == 0


@pytest.mark.asyncio
async def test_chat_scroll_does_not_reanchor_during_text_selection(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}") for idx in range(40)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test(size=(80, 20)) as pilot:
        await _wait_until(pilot.pause, lambda: app.query_one("#chat", ChatScroll))
        chat = app.query_one("#chat", ChatScroll)
        await _wait_until(pilot.pause, lambda: chat.max_scroll_y > 0)

        chat.anchor()
        await pilot.pause()
        assert chat.is_at_bottom

        app.screen._selecting = True

        # A selection drag scrolling up must release the anchor so the view
        # can move away from the bottom instead of snapping back.
        chat.scroll_y = chat.scroll_y - 1
        assert chat._anchor_released

        # Re-anchoring is suppressed while a selection is in progress.
        chat.anchor()
        assert chat._anchor_released

        # Once the selection ends, anchoring works again.
        app.screen._selecting = False
        chat.anchor()
        assert not chat._anchor_released
