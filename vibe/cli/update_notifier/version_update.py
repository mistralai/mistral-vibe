from __future__ import annotations

from packaging.version import InvalidVersion, Version

from vibe.cli.update_notifier.version_update_gateway import (
    DEFAULT_GATEWAY_MESSAGES,
    VersionUpdate,
    VersionUpdateGateway,
    VersionUpdateGatewayCause,
    VersionUpdateGatewayError,
)


class VersionUpdateError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _parse_version(raw: str) -> Version | None:
    try:
        return Version(raw.replace("-", "+"))
    except InvalidVersion:
        return None


def _describe_gateway_error(error: VersionUpdateGatewayError) -> str:
    if message := getattr(error, "user_message", None):
        return message

    cause = getattr(error, "cause", VersionUpdateGatewayCause.UNKNOWN)
    if isinstance(cause, VersionUpdateGatewayCause):
        return DEFAULT_GATEWAY_MESSAGES.get(
            cause, DEFAULT_GATEWAY_MESSAGES[VersionUpdateGatewayCause.UNKNOWN]
        )

    return DEFAULT_GATEWAY_MESSAGES[VersionUpdateGatewayCause.UNKNOWN]


async def is_version_update_available(
    version_update_notifier: VersionUpdateGateway, current_version: str
) -> VersionUpdate | None:
    try:
        update = await version_update_notifier.fetch_update()
    except VersionUpdateGatewayError as error:
        raise VersionUpdateError(_describe_gateway_error(error)) from error

    if not update:
        return None

    latest_version = _parse_version(update.latest_version)
    current = _parse_version(current_version)

    if latest_version is None or current is None:
        return None

    return update if latest_version > current else None
