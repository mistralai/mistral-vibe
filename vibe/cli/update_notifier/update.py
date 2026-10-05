from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
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
from vibe.cli.update_notifier.gateway_factory import uv_tool_receipt_path

UPDATE_CACHE_TTL_SECONDS = 24 * 60 * 60

# Sentinel stored in `source_stored_at` to void the Rust client's `source`
# tag pairing; a real stored timestamp is never negative.
_UNPAIRED_SOURCE_TAG = -1


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
    previous = await repository.get()
    timestamp = get_current_timestamp()
    # This write replaces the entry's answer without knowing a manager, so it
    # voids the Rust client's `source` pairing (-1 never matches a stored
    # timestamp): a stale tag must not keep speaking for the new version, not
    # even when the rewrite lands in the same whole second as the tagged write.
    if previous is None:
        await repository.set(
            UpdateCache(
                latest_version=version,
                stored_at_timestamp=timestamp,
                source_stored_at=_UNPAIRED_SOURCE_TAG,
            )
        )
        return
    await repository.set(
        replace(
            previous,
            latest_version=version,
            stored_at_timestamp=timestamp,
            source_stored_at=_UNPAIRED_SOURCE_TAG,
        )
    )


async def get_pending_update_from_cache(
    repository: UpdateCacheRepository, current_version: str
) -> str | None:
    current = _parse_version(current_version)
    if current is None:
        return None

    cache = await repository.get()
    if cache is None:
        return None

    latest = _parse_version(cache.latest_version)
    if latest is None or latest <= current:
        return None

    if cache.dismissed_version == cache.latest_version:
        return None

    return cache.latest_version


async def mark_update_as_dismissed(
    repository: UpdateCacheRepository, version: str
) -> None:
    cache = await repository.get()
    if cache is None:
        return
    await repository.set(replace(cache, dismissed_version=version))


async def get_update_if_available(
    update_notifier: UpdateGateway,
    current_version: str,
    update_cache_repository: UpdateCacheRepository,
    get_current_timestamp: Callable[[], int] = lambda: int(time.time()),
    *,
    force_check: bool = False,
) -> UpdateAvailability | None:
    current = _parse_version(current_version)
    if current is None:
        return None

    if not force_check and (update_cache := await update_cache_repository.get()):
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
FORCE_REINSTALL_COMMAND = "uv tool install --force mistral-vibe@latest"
INSTALLED_VERSION_COMMAND = "vibe --version"


async def do_update(latest_version: str) -> bool:
    for command in UPDATE_COMMANDS:
        await _run_shell(command)
    return await _is_installed(latest_version)


def is_uv_tool_install() -> bool:
    return uv_tool_receipt_path().is_file()


async def force_reinstall_latest(latest_version: str) -> bool:
    await _run_shell(FORCE_REINSTALL_COMMAND)
    return await _is_installed(latest_version)


async def _is_installed(latest_version: str) -> bool:
    latest = _parse_version(latest_version)
    installed = await _read_installed_version()
    return latest is not None and installed is not None and installed >= latest


async def _read_installed_version() -> Version | None:
    returncode, stdout = await _run_shell(INSTALLED_VERSION_COMMAND)
    if returncode != 0 or not (words := stdout.decode(errors="replace").split()):
        return None
    return _parse_version(words[-1])


async def _run_shell(command: str) -> tuple[int | None, bytes]:
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    return await _communicate(process)


async def _communicate(process: asyncio.subprocess.Process) -> tuple[int | None, bytes]:
    try:
        stdout, _ = await process.communicate()
    except asyncio.CancelledError:
        await _terminate(process)
        raise
    return process.returncode, stdout


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
