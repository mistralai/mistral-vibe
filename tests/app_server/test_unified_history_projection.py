"""Public effect entries projected from the runtime's settled tool records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mistralai_vibe_local_harness.vibe._session_api_entries import convert_history_entry
from mistralai_vibe_local_harness.vibe._session_api_state import HistoryIndex
from vibe.app_server._session_api_projection import ApiHistoryProjection
from vibe.app_server._unified_history_projection import project_raw_history_entry
from vibe.app_server.models import (
    CompletedEffectState,
    FailedEffectState,
    PublicEffectEntry,
)

OUTPUT = "first line\nsecond line"


def _connector_effect(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "effect",
        "id": "effect-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 2,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "inputEntryId": None,
        "title": "connector_linear.list_issues",
        "detail": {
            "kind": "tool",
            "toolName": "connector_linear.list_issues",
            "input": {"team": "VIB"},
            "display": {
                "summary": "connector_linear.list_issues",
                "message": "connector_linear.list_issues",
                "statusText": "Running connector_linear.list_issues",
            },
        },
        "state": state,
    }


def _output() -> dict[str, Any]:
    return {
        "type": "success",
        "content": [{"type": "text", "text": OUTPUT}],
        "structured_content": {"rows": 2},
    }


def _project(state: dict[str, Any]) -> PublicEffectEntry:
    (entry,) = project_raw_history_entry(_connector_effect(state), (Path("/work"),))
    assert isinstance(entry, PublicEffectEntry)
    return entry


_SETTLED_STATES = [
    {
        "status": "completed",
        "output": _output(),
        "display": {"success": True, "message": "done"},
    },
    {
        "status": "failed",
        "error": {"code": "tool_failed", "message": "boom"},
        "output": {**_output(), "type": "failure"},
        "display": {"success": False, "message": "boom"},
    },
]


@pytest.mark.parametrize("state", _SETTLED_STATES, ids=["completed", "failed"])
def test_a_settled_record_without_output_text_shows_its_content_text(
    state: dict[str, Any],
) -> None:
    """*Prepare*: A settled tool record that keeps its text only in output.content.
    *Do*: Project it.
    *Assert*: Clients get that text as outputText, and the structured output
        the rich views read is kept.
    """
    # Do
    entry = _project(state)

    # Assert
    assert isinstance(entry.state, CompletedEffectState | FailedEffectState)
    assert entry.state.output_text == OUTPUT
    wire = entry.model_dump(mode="json", by_alias=True)["state"]
    assert wire["outputText"] == OUTPUT
    assert wire["output"]["structured_content"] == {"rows": 2}


def test_a_record_saved_with_its_output_text_keeps_it() -> None:
    """*Prepare*: A completed record saved with its outputText.
    *Do*: Project it.
    *Assert*: The saved outputText is what clients get.
    """
    # Do
    entry = _project({
        "status": "completed",
        "output": _output(),
        "outputText": "saved text",
        "display": {"success": True, "message": "done"},
    })

    # Assert
    assert isinstance(entry.state, CompletedEffectState)
    assert entry.state.output_text == "saved text"


@pytest.mark.parametrize("state", _SETTLED_STATES, ids=["completed", "failed"])
def test_a_session_api_client_gets_the_content_text_as_output_text(
    state: dict[str, Any],
) -> None:
    """*Prepare*: A settled tool record without outputText, as Session API
        history entries.
    *Do*: Project them as a Session API read does.
    *Assert*: The effect's outputText is its content text.
    """
    # Prepare
    entries = convert_history_entry(_connector_effect(state), HistoryIndex(), {})
    projection = ApiHistoryProjection("session-1", [Path("/work")])

    # Do
    (entry,) = projection.reset(entries)

    # Assert
    wire = entry.model_dump(mode="json", by_alias=True)["state"]
    assert wire["status"] == state["status"]
    assert wire["outputText"] == OUTPUT
