from __future__ import annotations

import pytest

from vibe.cli.textual_ui.widgets.chat_input.glow import (
    GLOW_COLORS,
    STEPS_PER_STOP,
    glow_color,
)

HEX_LENGTH = 7


def test_glow_starts_at_the_first_ramp_stop():
    assert glow_color(0) == GLOW_COLORS[0]


def test_glow_reaches_the_last_ramp_stop_at_the_turn():
    turn = (len(GLOW_COLORS) - 1) * STEPS_PER_STOP
    assert glow_color(turn) == GLOW_COLORS[-1]


def test_glow_returns_to_the_first_stop_after_a_full_cycle():
    cycle = (len(GLOW_COLORS) - 1) * STEPS_PER_STOP * 2
    assert glow_color(cycle) == GLOW_COLORS[0]


def test_glow_is_symmetric_around_the_turn():
    turn = (len(GLOW_COLORS) - 1) * STEPS_PER_STOP
    assert glow_color(turn - 3) == glow_color(turn + 3)


@pytest.mark.parametrize("step", range(0, 80, 7))
def test_glow_always_returns_a_hex_colour(step: int):
    color = glow_color(step)
    assert len(color) == HEX_LENGTH
    assert color.startswith("#")
    int(color[1:], 16)


def test_glow_blends_between_stops_rather_than_snapping():
    midpoint = glow_color(STEPS_PER_STOP // 2)
    assert midpoint not in (GLOW_COLORS[0], GLOW_COLORS[1])


def test_glow_is_deterministic_for_a_given_step():
    assert [glow_color(7) for _ in range(5)] == [glow_color(7)] * 5


def test_single_stop_ramp_does_not_divide_by_zero():
    assert glow_color(5, colors=["#ABCDEF"]) == "#ABCDEF"


def test_empty_ramp_returns_empty_string():
    assert glow_color(5, colors=[]) == ""
