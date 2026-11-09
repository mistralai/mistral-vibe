"""Tests for the ``vibe.artifact_*`` telemetry events (SessionTelemetry)."""

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


def _events(
    telemetry_events: list[dict[str, Any]], event_name: str
) -> list[dict[str, Any]]:
    return [e for e in telemetry_events if e["event_name"] == event_name]


def test_record_artifact_detected(telemetry_events: list[dict[str, Any]]) -> None:
    service = _service()

    service.record_artifact_detected(
        provider="github", cli_source="system", command_matched=True, message_id="msg-1"
    )

    (event,) = _events(telemetry_events, "vibe.artifact_detected")
    props = event["properties"]
    assert props["provider"] == "github"
    assert props["cli_source"] == "system"
    assert props["command_matched"] is True
    assert props["message_id"] == "msg-1"


def test_record_artifact_created(telemetry_events: list[dict[str, Any]]) -> None:
    service = _service()

    service.record_artifact_created(
        provider="gitlab",
        is_draft=True,
        host_type="enterprise",
        repo_id_resolved=True,
        has_title=False,
    )

    (event,) = _events(telemetry_events, "vibe.artifact_created")
    props = event["properties"]
    assert props["provider"] == "gitlab"
    assert props["is_draft"] is True
    assert props["host_type"] == "enterprise"
    assert props["repo_id_resolved"] is True
    assert props["has_title"] is False
    assert props["message_id"] is None
    assert props["source"] == "detected"


def test_record_artifact_created_discovered_source(
    telemetry_events: list[dict[str, Any]],
) -> None:
    """Phase 2 discovery reuses ``vibe.artifact_created`` with
    ``source=discovered`` — no separate ``vibe.artifact_discovered`` event
    (``discovered`` is not in the closed past-tense verb list).
    """
    service = _service()

    service.record_artifact_created(
        provider="github",
        is_draft=False,
        host_type="github_com",
        repo_id_resolved=False,
        has_title=True,
        source="discovered",
    )

    (event,) = _events(telemetry_events, "vibe.artifact_created")
    assert event["properties"]["source"] == "discovered"


def test_record_artifact_mark_viewed(telemetry_events: list[dict[str, Any]]) -> None:
    """Phase 3+ event — the method exists now, the Desktop RPC calls it later."""
    service = _service()

    service.record_artifact_mark_viewed(nb_artifacts=3, nb_already_viewed=1)

    (event,) = _events(telemetry_events, "vibe.artifact_mark_viewed")
    props = event["properties"]
    assert props["nb_artifacts"] == 3
    assert props["nb_already_viewed"] == 1


def test_record_artifact_detection_failed(
    telemetry_events: list[dict[str, Any]],
) -> None:
    service = _service()

    service.record_artifact_detection_failed(
        error_type="TimeoutError", provider="gitlab", stage="repo_id_resolve"
    )

    (event,) = _events(telemetry_events, "vibe.artifact_detection_failed")
    props = event["properties"]
    assert props["error_type"] == "TimeoutError"
    assert props["provider"] == "gitlab"
    assert props["stage"] == "repo_id_resolve"


def test_artifact_events_carry_no_pii(monkeypatch: Any) -> None:
    """Raw payloads are enums and counters — no URLs, repo names or PR numbers.

    Captures the properties before the base metadata merge, so the assertion
    covers exactly what the record_* methods put on the wire.
    """
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        TelemetryClient,
        "send_telemetry_event",
        lambda self, event_name, properties, **_: captured.append((
            event_name,
            properties,
        )),
    )
    service = _service()

    service.record_artifact_detected(
        provider="github", cli_source="bundled", command_matched=True
    )
    service.record_artifact_created(
        provider="github",
        is_draft=False,
        host_type="github_com",
        repo_id_resolved=False,
        has_title=True,
    )
    service.record_artifact_mark_viewed(nb_artifacts=2, nb_already_viewed=0)
    service.record_artifact_detection_failed(
        error_type="ValueError", provider="unknown", stage="output_extract"
    )

    payloads = dict(captured)
    assert set(payloads["vibe.artifact_detected"]) == {
        "provider",
        "cli_source",
        "command_matched",
        "message_id",
    }
    assert set(payloads["vibe.artifact_created"]) == {
        "provider",
        "is_draft",
        "host_type",
        "repo_id_resolved",
        "has_title",
        "message_id",
        "source",
    }
    assert set(payloads["vibe.artifact_mark_viewed"]) == {
        "nb_artifacts",
        "nb_already_viewed",
    }
    assert set(payloads["vibe.artifact_detection_failed"]) == {
        "error_type",
        "provider",
        "stage",
    }
