from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Protocol


@dataclass(frozen=True, slots=True)
class VersionUpdate:
    latest_version: str


class VersionUpdateGatewayCause(StrEnum):
    @staticmethod
    def _generate_next_value_(
        name: str, start: int, count: int, last_values: list[str]
    ) -> str:
        return name.lower()

    TOO_MANY_REQUESTS = auto()
    FORBIDDEN = auto()
    NOT_FOUND = auto()
    REQUEST_FAILED = auto()
    ERROR_RESPONSE = auto()
    INVALID_RESPONSE = auto()
    UNKNOWN = auto()


DEFAULT_GATEWAY_MESSAGES: dict[VersionUpdateGatewayCause, str] = {
    VersionUpdateGatewayCause.TOO_MANY_REQUESTS: "Rate limit exceeded while checking for updates.",
    VersionUpdateGatewayCause.FORBIDDEN: "Request was forbidden while checking for updates.",
    VersionUpdateGatewayCause.NOT_FOUND: "Unable to fetch the releases. Please check your permissions.",
    VersionUpdateGatewayCause.REQUEST_FAILED: "Network error while checking for updates.",
    VersionUpdateGatewayCause.ERROR_RESPONSE: "Unexpected response received while checking for updates.",
    VersionUpdateGatewayCause.INVALID_RESPONSE: "Received an invalid response while checking for updates.",
    VersionUpdateGatewayCause.UNKNOWN: "Unable to determine whether an update is available.",
}


class VersionUpdateGatewayError(Exception):
    def __init__(
        self, *, cause: VersionUpdateGatewayCause, message: str | None = None
    ) -> None:
        self.cause = cause
        self.user_message = message
        detail = message or DEFAULT_GATEWAY_MESSAGES.get(
            cause, DEFAULT_GATEWAY_MESSAGES[VersionUpdateGatewayCause.UNKNOWN]
        )
        super().__init__(detail)


class VersionUpdateGateway(Protocol):
    async def fetch_update(self) -> VersionUpdate | None: ...
