from __future__ import annotations

import time

import pytest
from textual.widgets import Button

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import build_test_agent_loop
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse
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
            is_pro_plan=True,
            advertise_pro_plan=False,
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
                lambda current_count=current_count: len(app.query(UserMessage))
                > current_count,
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

    async with app.run_test(size=(120, 80)) as pilot:
        await _wait_for_load_more(app, pilot.pause)
        chat = app.query_one("#chat", ChatScroll)
        assert chat.max_scroll_y == 0
        assert chat.scroll_y == 0
