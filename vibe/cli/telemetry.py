from __future__ import annotations

from typing import Any, Protocol


class ClientTelemetry(Protocol):
    def send_telemetry_event(
        self,
        event_name: str,
        properties: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> None: ...

    def build_request_metadata(
        self, message_id: str | None = None
    ) -> dict[str, Any]: ...
