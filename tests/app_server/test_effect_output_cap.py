from __future__ import annotations

from typing import Any, cast

from pydantic import JsonValue

from vibe.app_server._effect_output_cap import (
    EFFECT_OUTPUT_TEXT_MAX_CHARS,
    TRUNCATION_MARKER,
    capped_effect_entry,
    capped_output_append,
)
from vibe.app_server.models import (
    CompletedEffectState,
    PublicEffectEntry,
    PublicHistoryEntry,
    RunningEffectState,
    validate_history_entry,
)

_HUGE = "x" * (EFFECT_OUTPUT_TEXT_MAX_CHARS * 3)
_CAPPED = f"{'x' * EFFECT_OUTPUT_TEXT_MAX_CHARS}{TRUNCATION_MARKER}"


def _effect(state: dict[str, Any]) -> PublicHistoryEntry:
    return validate_history_entry({
        "id": "effect-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "type": "effect",
        "title": "mcp_grafana.list_loki_label_values",
        "createdAt": 1,
        "updatedAt": 2,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "detail": {
            "kind": "tool",
            "toolName": "mcp_grafana.list_loki_label_values",
            "input": {"labelName": "app"},
            "display": {
                "summary": "mcp_grafana.list_loki_label_values",
                "message": "mcp_grafana.list_loki_label_values",
                "statusText": "Running mcp_grafana.list_loki_label_values",
                "verb": "Running",
                "settledVerb": "Ran",
                "settledMessage": "mcp_grafana.list_loki_label_values",
            },
        },
        "state": state,
    })


def _completed(output: JsonValue, output_text: str) -> PublicHistoryEntry:
    return _effect({
        "status": "completed",
        "output": output,
        "outputText": output_text,
        "display": {"success": True, "message": "done"},
    })


def _mcp_output(text: str) -> JsonValue:
    return {"type": "success", "content": [{"type": "text", "text": text}]}


def _completed_state(entry: PublicHistoryEntry) -> CompletedEffectState:
    assert isinstance(entry, PublicEffectEntry)
    assert isinstance(entry.state, CompletedEffectState)
    return entry.state


def test_huge_tool_output_is_capped_in_text_and_structured_output() -> None:
    entry = capped_effect_entry(_completed(_mcp_output(_HUGE), _HUGE))

    state = _completed_state(entry)
    assert state.output_text == _CAPPED
    assert state.output == _mcp_output(_CAPPED)


def test_output_within_the_limit_is_returned_unchanged() -> None:
    entry = _completed(_mcp_output("small"), "small")

    assert capped_effect_entry(entry) is entry


def test_capping_is_idempotent() -> None:
    capped = capped_effect_entry(_completed(_mcp_output(_HUGE), _HUGE))

    assert capped_effect_entry(capped) is capped


def test_structured_output_over_budget_after_capping_strings_is_dropped() -> None:
    many_short_strings: JsonValue = cast(
        JsonValue, ["label"] * EFFECT_OUTPUT_TEXT_MAX_CHARS
    )

    entry = capped_effect_entry(_completed(many_short_strings, "labels"))

    state = _completed_state(entry)
    assert state.output is None
    assert state.output_text == "labels"


def test_running_effect_output_text_is_capped() -> None:
    entry = capped_effect_entry(_effect({"status": "running", "outputText": _HUGE}))

    assert isinstance(entry, PublicEffectEntry)
    assert isinstance(entry.state, RunningEffectState)
    assert entry.state.output_text == _CAPPED


def test_capped_text_keeps_the_start_of_the_output() -> None:
    output_text = f"<skill-marker>{_HUGE}"

    entry = capped_effect_entry(_completed(None, output_text))

    assert _completed_state(entry).output_text.startswith("<skill-marker>")


def test_streamed_append_is_cut_at_the_limit_and_marked_once() -> None:
    almost_full = "x" * (EFFECT_OUTPUT_TEXT_MAX_CHARS - 2)

    crossing = capped_output_append(almost_full, "abcd")
    after = capped_output_append(f"{almost_full}{crossing}", "more")

    assert crossing == f"ab{TRUNCATION_MARKER}"
    assert after == ""


def test_streamed_append_within_the_limit_is_kept() -> None:
    assert capped_output_append("abc", "def") == "def"


def test_non_effect_entries_are_returned_unchanged() -> None:
    message = validate_history_entry({
        "id": "message-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "type": "message",
        "role": "assistant",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed",
        "content": [{"type": "text", "text": _HUGE}],
    })

    assert capped_effect_entry(message) is message
