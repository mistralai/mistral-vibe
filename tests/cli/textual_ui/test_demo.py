from __future__ import annotations

import pytest
from textual.widget import Widget

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.app_server.models import (
    BlockedEffectState,
    CancelledEffectState,
    CompletedEffectState,
    FailedEffectState,
    ImageContentBlock,
    PendingEffectState,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
    RunningEffectState,
    SkippedEffectState,
)
from vibe.cli.textual_ui import demo, stress
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    ReasoningMessage,
    UserMessage,
)
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.cli.textual_ui.windowing.history import history_entry_renders_widget

_DEMO_WIDGET_COUNTS: dict[type[Widget], int] = {
    UserMessage: 2,
    AssistantMessage: 1,
    ReasoningMessage: 2,
    ToolCallMessage: 9,
    ToolResultMessage: 9,
    CompactMessage: 1,
}
_STRESS_WIDGET_COUNTS = _DEMO_WIDGET_COUNTS | {UserMessage: 3}


def _history_widget_counts(root: Widget) -> dict[type[Widget], int]:
    return {
        widget_type: len(root.query(widget_type)) for widget_type in _DEMO_WIDGET_COUNTS
    }


def test_demo_entries_cover_every_renderable_history_shape() -> None:
    entries = demo.entries()

    assert all(history_entry_renders_widget(entry) for entry in entries)
    assert {type(entry) for entry in entries} == {
        PublicMessageEntry,
        PublicReasoningEntry,
        PublicEffectEntry,
        PublicCheckpointEntry,
    }
    assert {
        type(entry.state) for entry in entries if isinstance(entry, PublicEffectEntry)
    } == {
        PendingEffectState,
        RunningEffectState,
        BlockedEffectState,
        CompletedEffectState,
        FailedEffectState,
        CancelledEffectState,
        SkippedEffectState,
    }
    assert {
        entry.role for entry in entries if isinstance(entry, PublicMessageEntry)
    } == {"user", "assistant"}
    assert any(
        isinstance(block, ImageContentBlock)
        for entry in entries
        if isinstance(entry, PublicMessageEntry)
        for block in entry.content
    )


def test_stress_entries_repeat_demo_order_with_unique_ids() -> None:
    entries = demo.entries()
    repeated = list(demo.stress_entries(len(entries) * 2 + 1))

    assert [entry.id for entry in repeated[: len(entries)]] == [
        f"stress-{index:06}-{entry.id}" for index, entry in enumerate(entries)
    ]
    assert [entry.type for entry in repeated] == [
        entries[index % len(entries)].type for index in range(len(repeated))
    ]
    assert len({entry.id for entry in repeated}) == len(repeated)


def test_stress_count_matches_the_command_contract() -> None:
    assert stress.count("") == 100
    assert stress.count("invalid") == 100
    assert stress.count("-1") == 1
    assert stress.count("100001") == 100_000


@pytest.mark.asyncio
async def test_demo_command_mounts_the_full_fixture() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config(show_thinking_nodes=True))

    async with app.run_test() as pilot:
        await pilot.pause()
        before = _history_widget_counts(app._messages_area)

        assert await app._handle_command("/demo")
        await pilot.pause()

        after = _history_widget_counts(app._messages_area)

    assert {
        widget_type: after[widget_type] - before[widget_type]
        for widget_type in _DEMO_WIDGET_COUNTS
    } == _DEMO_WIDGET_COUNTS


@pytest.mark.asyncio
async def test_stress_command_repeats_the_demo_fixture() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config(show_thinking_nodes=True))

    async with app.run_test() as pilot:
        await pilot.pause()
        before = _history_widget_counts(app._messages_area)

        assert await app._handle_command("/stress 15")
        task = app.stress_task
        assert task is not None
        await task
        await pilot.pause()

        after = _history_widget_counts(app._messages_area)

    assert {
        widget_type: after[widget_type] - before[widget_type]
        for widget_type in _DEMO_WIDGET_COUNTS
    } == _STRESS_WIDGET_COUNTS
