from __future__ import annotations

from typing import cast

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.calm_reveal import (
    DIMMED_OPACITY,
    FOCUSED_OPACITY,
    CalmRevealContainer,
)
from vibe.cli.textual_ui.widgets.calm_status import CalmStatus
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import AssistantMessage

MULTI_BLOCK = "First paragraph here.\n\nSecond paragraph now.\n\nThird and final block."


def _calm_app(*, motion: bool = False, pace: str = "Swift") -> VibeApp:
    config = build_test_vibe_config(
        calm_mode_enabled=True, calm_motion_enabled=motion, calm_pace_label=pace
    )
    return build_test_vibe_app(config=config)


async def _mount_calm(
    pilot, content: str = MULTI_BLOCK
) -> tuple[AssistantMessage, CalmRevealContainer]:
    app = pilot.app
    messages = app.query_one("#messages")
    msg = AssistantMessage(content, calm_mode=True)
    await messages.mount(msg)
    container = await msg.build_calm_reveal()
    assert container is not None
    await pilot.pause()
    return msg, container


@pytest.mark.asyncio
async def test_only_first_block_revealed_and_focused() -> None:
    async with _calm_app().run_test() as pilot:
        _, container = await _mount_calm(pilot)
        blocks = container._blocks

        assert len(blocks) == 3
        assert blocks[0]._revealed is True
        assert blocks[0].styles.display == "block"
        assert blocks[0].styles.opacity == FOCUSED_OPACITY
        for block in blocks[1:]:
            assert block._revealed is False
            assert block.styles.display == "none"
        assert container.cursor == 0


@pytest.mark.asyncio
async def test_motion_on_opacity_climbs_to_full() -> None:
    async with _calm_app(motion=True, pace="Swift").run_test() as pilot:
        _, container = await _mount_calm(pilot)
        first = container._blocks[0]

        await pilot.pause(0.3)
        assert first.styles.opacity == pytest.approx(FOCUSED_OPACITY, abs=0.05)


@pytest.mark.asyncio
async def test_next_reveals_and_dims_previous() -> None:
    async with _calm_app().run_test() as pilot:
        _, container = await _mount_calm(pilot)
        blocks = container._blocks

        container.calm_next()
        await pilot.pause()

        assert container.cursor == 1
        assert blocks[1]._revealed is True
        assert blocks[1].styles.opacity == FOCUSED_OPACITY
        assert blocks[0].styles.opacity == DIMMED_OPACITY


@pytest.mark.asyncio
async def test_prev_refocuses_without_hiding() -> None:
    async with _calm_app().run_test() as pilot:
        _, container = await _mount_calm(pilot)
        blocks = container._blocks

        container.calm_next()
        await pilot.pause()
        container.calm_prev()
        await pilot.pause()

        assert container.cursor == 0
        assert blocks[0].styles.opacity == FOCUSED_OPACITY
        assert blocks[1]._revealed is True
        assert blocks[1].styles.opacity == DIMMED_OPACITY


@pytest.mark.asyncio
async def test_arrows_navigate_only_when_input_empty() -> None:
    async with _calm_app().run_test() as pilot:
        app = cast(VibeApp, pilot.app)
        _, container = await _mount_calm(pilot)
        input_container = app.query_one("#input-container", ChatInputContainer)
        text_area = input_container.input_widget
        assert text_area is not None
        text_area.focus()

        text_area.text = "typing"
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert container.cursor == 0

        text_area.text = ""
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert container.cursor == 1


@pytest.mark.asyncio
async def test_escape_reveals_all() -> None:
    async with _calm_app().run_test() as pilot:
        _, container = await _mount_calm(pilot)
        blocks = container._blocks

        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        for block in blocks:
            assert block._revealed is True
            assert block.styles.display == "block"
        assert container.cursor == len(blocks) - 1


@pytest.mark.asyncio
async def test_motion_off_reveals_are_instant() -> None:
    async with _calm_app(motion=True, pace="Calm").run_test() as pilot:
        app = cast(VibeApp, pilot.app)
        _, container = await _mount_calm(pilot)
        blocks = container._blocks

        await pilot.pause(0.7)
        await app.action_calm_toggle_motion()
        await pilot.pause()
        container.calm_next()
        await pilot.pause()

        assert blocks[1].styles.opacity == FOCUSED_OPACITY
        assert blocks[0].styles.opacity == DIMMED_OPACITY


@pytest.mark.asyncio
async def test_calm_off_renders_original_path() -> None:
    config = build_test_vibe_config(calm_mode_enabled=False)
    async with build_test_vibe_app(config=config).run_test() as pilot:
        app = pilot.app
        messages = app.query_one("#messages")
        msg = AssistantMessage("Just one paragraph.")
        await messages.mount(msg)
        await msg.write_initial_content()
        await pilot.pause()

        assert msg.calm_mode is False
        assert msg.calm_container is None
        assert msg._markdown is not None
        assert list(app.query(CalmRevealContainer)) == []


def test_calm_status_shows_label_only_when_enabled() -> None:
    status = CalmStatus()
    assert status.display is False

    status.set_enabled(True)
    assert status.display is True
    assert "calm mode" in str(status.render())

    status.set_enabled(False)
    assert status.display is False


@pytest.mark.asyncio
async def test_slash_calm_command_toggles_mode() -> None:
    config = build_test_vibe_config(calm_mode_enabled=False)
    async with build_test_vibe_app(config=config).run_test() as pilot:
        app = pilot.app

        assert app.calm_mode_enabled is False

        text_area = app.query_one("#input-container", ChatInputContainer).input_widget
        assert text_area is not None
        text_area.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        await pilot.press("c", "a", "l", "m")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        assert app.calm_mode_enabled is True
