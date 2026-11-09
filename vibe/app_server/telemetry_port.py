from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ClientTelemetryEvent:
    name: str
    properties: Mapping[str, object]
    correlation_id: str | None = None


class ClientTelemetry(Protocol):
    def log(self, event: ClientTelemetryEvent) -> None: ...
