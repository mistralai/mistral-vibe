from __future__ import annotations

from tests.conftest import build_test_vibe_config
from vibe.core.experiments.active import ExperimentName
from vibe.core.telemetry.send import TelemetryClient


def test_build_client_event_metadata_omits_experiments_when_empty() -> None:
    config = build_test_vibe_config(enable_telemetry=True)
    client = TelemetryClient(
        config_getter=lambda: config, experiments_getter=lambda: {}
    )
    metadata = client.build_client_event_metadata()
    assert "experiments" not in metadata


def test_build_client_event_metadata_includes_experiments_when_present() -> None:
    config = build_test_vibe_config(enable_telemetry=True)
    sp_key = ExperimentName.SYSTEM_PROMPT.value
    client = TelemetryClient(
        config_getter=lambda: config, experiments_getter=lambda: {sp_key: "1"}
    )
    metadata = client.build_client_event_metadata()
    assert metadata["experiments"] == {sp_key: "1"}


def test_build_client_event_metadata_works_without_getter() -> None:
    config = build_test_vibe_config(enable_telemetry=True)
    client = TelemetryClient(config_getter=lambda: config)
    metadata = client.build_client_event_metadata()
    assert "experiments" not in metadata
