from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from vibe.app_server._service_resources import TelemetryResource
from vibe.app_server.telemetry_port import ClientTelemetryEvent


@pytest.mark.parametrize(
    ("correlation_id", "correlate_last_request"), [(None, False), ("request-1", True)]
)
def test_log_adapts_shared_telemetry_contract(
    correlation_id: str | None, correlate_last_request: bool
) -> None:
    resource = TelemetryResource(MagicMock(), MagicMock())
    event = ClientTelemetryEvent(
        name="vibe.test", properties={"status": "ok"}, correlation_id=correlation_id
    )

    with patch.object(resource, "record") as record:
        resource.log(event)

    record.assert_called_once_with(
        "vibe.test", {"status": "ok"}, correlate_last_request=correlate_last_request
    )
