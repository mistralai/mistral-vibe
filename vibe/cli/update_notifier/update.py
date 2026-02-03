from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import time

from packaging.version import InvalidVersion, Version

from vibe.cli.update_notifier import (
    DEFAULT_GATEWAY_MESSAGES,
    UpdateCache,
    UpdateCacheRepository,
    UpdateGateway,
    UpdateGatewayCause,
    UpdateGatewayError,
)

UPDATE_CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class UpdateAvailability:
    latest_version: str
    should_notify: bool


class UpdateError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _parse_version(raw: str) -> Version | None:
    try:
        return Version(raw.replace("-", "+"))
    except InvalidVersion:
        return None


def _describe_gateway_error(error: UpdateGatewayError) -> str:
    if message := getattr(error, "user_message", None):
        return message

    cause = getattr(error, "cause", UpdateGatewayCause.UNKNOWN)
    if isinstance(cause, UpdateGatewayCause):
        return DEFAULT_GATEWAY_MESSAGES.get(
            cause, DEFAULT_GATEWAY_MESSAGES[UpdateGatewayCause.UNKNOWN]
        )

    return DEFAULT_GATEWAY_MESSAGES[UpdateGatewayCause.UNKNOWN]


def _is_cache_fresh(
    cache: UpdateCache, get_current_timestamp: Callable[[], int]
) -> bool:
    return (
        cache.stored_at_timestamp > get_current_timestamp() - UPDATE_CACHE_TTL_SECONDS
    )


def _get_cached_update_if_any(
    cache: UpdateCache, current: Version
) -> UpdateAvailability | None:
    latest_version_in_cache = _parse_version(cache.latest_version)
    if latest_version_in_cache is None or latest_version_in_cache <= current:
        return None

    return UpdateAvailability(latest_version=cache.latest_version, should_notify=False)


async def _write_update_cache(
    repository: UpdateCacheRepository,
    version: str,
    get_current_timestamp: Callable[[], int],
) -> None:
    await repository.set(
        UpdateCache(latest_version=version, stored_at_timestamp=get_current_timestamp())
    )


async def get_update_if_available(
    update_notifier: UpdateGateway,
    current_version: str,
    update_cache_repository: UpdateCacheRepository,
    get_current_timestamp: Callable[[], int] = lambda: int(time.time()),
) -> UpdateAvailability | None:
    if not (current := _parse_version(current_version)):
        return None

    if update_cache := await update_cache_repository.get():
        if _is_cache_fresh(update_cache, get_current_timestamp):
            return _get_cached_update_if_any(update_cache, current)

    try:
        update = await update_notifier.fetch_update()
    except UpdateGatewayError as error:
        await _write_update_cache(
            update_cache_repository, current_version, get_current_timestamp
        )
        raise UpdateError(_describe_gateway_error(error)) from error

    if not update:
        await _write_update_cache(
            update_cache_repository, current_version, get_current_timestamp
        )
        return None

    if not (latest_version := _parse_version(update.latest_version)):
        return None

    if latest_version <= current:
        await _write_update_cache(
            update_cache_repository, current_version, get_current_timestamp
        )
        return None

    await _write_update_cache(
        update_cache_repository, update.latest_version, get_current_timestamp
    )

    return UpdateAvailability(latest_version=update.latest_version, should_notify=True)


UPDATE_COMMANDS = ["uv tool upgrade mistral-vibe", "brew upgrade mistral-vibe"]


async def do_update() -> bool:
    for command in UPDATE_COMMANDS:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        await process.wait()
        if process.returncode == 0:
            return True
    return False
