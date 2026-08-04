from __future__ import annotations

from vibe.core.telemetry.types import TeleportFailureDetails


class ServiceTeleportError(Exception):
    """Base exception for teleport errors."""

    def __init__(
        self, message: str, *, telemetry_details: TeleportFailureDetails | None = None
    ) -> None:
        super().__init__(message)
        self.telemetry_details = telemetry_details or {}


class ServiceTeleportNotSupportedError(ServiceTeleportError):
    pass
