from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.core.pawgress.events import (
    ControlAction,
    ControlMessage,
    Criterion,
    IslandState,
    IslandStatus,
    encode_jsonl,
    parse_control,
    parse_island_state,
)


def test_island_state_jsonl_round_trip_preserves_value():
    state = IslandState(
        goal="Fix the failing cache test",
        state=IslandStatus.VERIFYING,
        detail="running pytest",
        criteria=[
            Criterion(label="Verification", done=False, progress="3/5"),
            Criterion(label="No new deps", done=False),
        ],
        iteration="2/8",
        elapsed="00:42",
        cost=0.12,
        budget=1.0,
        evidence=["5/5 verification runs passed"],
    )

    line = encode_jsonl(state)

    assert line.endswith("\n")
    assert parse_island_state(line) == state


def test_control_message_jsonl_round_trip_preserves_value():
    message = ControlMessage(action=ControlAction.FOCUS_VIBE)

    line = encode_jsonl(message)

    assert line.endswith("\n")
    assert parse_control(line) == message


def test_island_status_wire_values_are_lowercase():
    assert IslandStatus.WORKING == "working"
    assert IslandStatus.VERIFYING == "verifying"
    assert IslandStatus.BLOCKED == "blocked"
    assert IslandStatus.COMPLETED == "completed"


def test_control_action_wire_values_are_lowercase():
    assert ControlAction.PAUSE == "pause"
    assert ControlAction.STOP == "stop"
    assert ControlAction.FOCUS_VIBE == "focus_vibe"


def test_island_state_rejects_unknown_keys():
    payload = '{"type": "island_state", "goal": "g", "state": "working", "surprise": 1}'

    with pytest.raises(ValidationError):
        parse_island_state(payload)


def test_control_message_rejects_unknown_keys():
    payload = '{"type": "control", "action": "pause", "surprise": 1}'

    with pytest.raises(ValidationError):
        parse_control(payload)


def test_criterion_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        Criterion.model_validate({"label": "x", "mystery": True})


def test_island_state_context_usage_roundtrip():
    state = IslandState(
        goal="g",
        state=IslandStatus.WORKING,
        context_tokens=14_000,
        context_max=200_000,
        usage_used=170_000,
        usage_limit=500_000,
        usage_reset_seconds=40,
    )
    parsed = parse_island_state(encode_jsonl(state))
    assert parsed.context_tokens == 14_000
    assert parsed.context_max == 200_000
    assert parsed.usage_used == 170_000
    assert parsed.usage_limit == 500_000
    assert parsed.usage_reset_seconds == 40


def test_island_state_old_lines_without_new_fields_still_parse():
    line = '{"type": "island_state", "goal": "g", "state": "working"}'
    parsed = parse_island_state(line)
    assert parsed.context_tokens is None
    assert parsed.usage_limit is None
