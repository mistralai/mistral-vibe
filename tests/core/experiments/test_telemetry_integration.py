from __future__ import annotations

from tests.conftest import build_test_vibe_config
from vibe import __version__
from vibe.core.experiments.active import ExperimentName
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import ExperimentAssignment
from vibe.core.utils import get_platform_id, get_platform_version


def _assert_system_metadata(metadata: dict[str, object]) -> None:
    assert metadata["os"] == get_platform_id()
    assert metadata["version"] == __version__
    if os_version := get_platform_version():
        assert metadata["os_version"] == os_version
    else:
        assert "os_version" not in metadata


def test_build_client_event_metadata_omits_experiments_when_empty() -> None:
    config = build_test_vibe_config(enable_telemetry=True)
    client = TelemetryClient(
        config_getter=lambda: config, experiments_getter=lambda: []
    )
    metadata = client.build_client_event_metadata()
    assert "experiments" not in metadata
    assert "experiment_assignments" not in metadata
    _assert_system_metadata(metadata)


def test_build_client_event_metadata_includes_experiments_when_present() -> None:
    config = build_test_vibe_config(enable_telemetry=True)
    sp_key = ExperimentName.SYSTEM_PROMPT.value
    assignment = ExperimentAssignment(
        experiment_id=sp_key,
        experiment_name=sp_key,
        variation_name="cli_2026-07_v2",
        variation_id=1,
    )
    client = TelemetryClient(
        config_getter=lambda: config, experiments_getter=lambda: [assignment]
    )
    metadata = client.build_client_event_metadata()
    # Legacy flat map stays for the existing exposures pipeline.
    assert metadata["experiments"] == {sp_key: "cli_2026-07_v2"}
    # New additive field carries the richer assignment data.
    assert metadata["experiment_assignments"] == [
        {
            "experiment_id": sp_key,
            "experiment_name": sp_key,
            "variation_name": "cli_2026-07_v2",
            "variation_id": 1,
        }
    ]
    _assert_system_metadata(metadata)


def test_build_client_event_metadata_works_without_getter() -> None:
    config = build_test_vibe_config(enable_telemetry=True)
    client = TelemetryClient(config_getter=lambda: config)
    metadata = client.build_client_event_metadata()
    assert "experiments" not in metadata
    _assert_system_metadata(metadata)
