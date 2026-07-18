from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.core.watchdog import Evidence, Incident, IncidentState, RunState


def test_run_state_round_trip_is_stable() -> None:
    state = RunState(
        run_id="run-1",
        session_id="session-1",
        epoch=1,
        incident=Incident(
            incident_id="incident-1",
            epoch=1,
            state=IncidentState.SUSPECTED,
            evidence=(
                Evidence(
                    detector="repeat-call",
                    fingerprint="abc",
                    observed_sequence=2,
                    facts={"tool": "bash"},
                ),
            ),
        ),
    )

    restored = RunState.model_validate_json(state.model_dump_json())

    assert restored == state


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RunState.model_validate({
            "run_id": "run-1",
            "session_id": "session-1",
            "unknown": True,
        })
