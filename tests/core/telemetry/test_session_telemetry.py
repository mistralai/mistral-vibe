from __future__ import annotations

from typing import Any

from tests.conftest import build_test_vibe_config
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.session import SessionTelemetry


def _service() -> SessionTelemetry:
    client = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    return SessionTelemetry(client, model="m1")


def test_claim_dedupes_and_mark_recorded_pre_claims() -> None:
    service = _service()
    assert service.claim("e1") is True
    # A second sight of the same effect never re-emits.
    assert service.claim("e1") is False
    service.mark_recorded("e2")
    assert service.claim("e2") is False


def test_records_use_the_active_model(telemetry_events: list[dict[str, Any]]) -> None:
    service = _service()

    service.record_tool_call_finished(
        tool_name="write_file",
        status="success",
        agent_profile_name="default",
        nb_files_created=1,
        nb_files_modified=0,
        file_extension=".py",
    )
    # A model switch is reflected in every subsequent event.
    service.set_model("m2")
    service.record_subagent_tool_call_finished(
        operation="spawn", outcome="success", profile_source="generic"
    )

    events = [
        e for e in telemetry_events if e["event_name"] == "vibe.tool_call_finished"
    ]
    assert len(events) == 2
    assert events[0]["properties"]["tool_name"] == "write_file"
    assert events[0]["properties"]["model"] == "m1"
    assert events[1]["properties"]["tool_name"] == "subagent.spawn"
    assert events[1]["properties"]["model"] == "m2"


def test_records_compaction_events(telemetry_events: list[dict[str, Any]]) -> None:
    service = _service()

    service.record_auto_compact_triggered(
        nb_context_tokens_before=100, auto_compact_threshold=200, status="success"
    )
    service.record_compaction_failed(reason="empty_summary")

    triggered = [
        e for e in telemetry_events if e["event_name"] == "vibe.auto_compact_triggered"
    ]
    failed = [
        e for e in telemetry_events if e["event_name"] == "vibe.compaction_failed"
    ]
    assert triggered[0]["properties"]["nb_context_tokens_before"] == 100
    assert triggered[0]["properties"]["auto_compact_threshold"] == 200
    assert failed[0]["properties"]["reason"] == "empty_summary"
