from __future__ import annotations

import pytest

from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_version_update_gateway import (
    FakeVersionUpdateGateway,
)
from vibe.cli.update_notifier import (
    UpdateCache,
    VersionUpdate,
    VersionUpdateGatewayCause,
    VersionUpdateGatewayError,
)
from vibe.cli.update_notifier.version_update import (
    VersionUpdateError,
    get_update_if_available,
)


@pytest.fixture
def current_timestamp() -> int:
    return 1765278683


@pytest.mark.asyncio
async def test_retrieves_the_latest_version_update_when_available() -> None:
    latest_update = "1.0.3"
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version=latest_update)
    )

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=FakeUpdateCacheRepository(),
    )

    assert update is not None
    assert update.latest_version == latest_update


@pytest.mark.asyncio
async def test_retrieves_nothing_when_the_current_version_is_the_latest() -> None:
    current_version = "1.0.0"
    latest_version = "1.0.0"
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version=latest_version)
    )

    update = await get_update_if_available(
        version_update_notifier,
        current_version=current_version,
        update_cache_repository=FakeUpdateCacheRepository(),
    )

    assert update is None


@pytest.mark.asyncio
async def test_retrieves_nothing_when_the_current_version_is_greater_than_the_latest() -> (
    None
):
    current_version = "0.2.0"
    latest_version = "0.1.2"
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version=latest_version)
    )

    update = await get_update_if_available(
        version_update_notifier,
        current_version=current_version,
        update_cache_repository=FakeUpdateCacheRepository(),
    )

    assert update is None


@pytest.mark.asyncio
async def test_retrieves_nothing_when_no_version_is_available() -> None:
    version_update_notifier = FakeVersionUpdateGateway(update=None)

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=FakeUpdateCacheRepository(),
    )

    assert update is None


@pytest.mark.asyncio
async def test_retrieves_nothing_when_latest_version_is_invalid() -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="invalid-version")
    )

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=FakeUpdateCacheRepository(),
    )

    assert update is None


@pytest.mark.asyncio
async def test_replaces_hyphens_with_plus_signs_in_latest_version_to_conform_with_PEP_440() -> (
    None
):
    version_update_notifier = FakeVersionUpdateGateway(
        # if we were not replacing hyphens with plus signs, this should fail for PEP 440
        update=VersionUpdate(latest_version="1.6.1-jetbrains")
    )

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=FakeUpdateCacheRepository(),
    )

    assert update is not None
    assert update.latest_version == "1.6.1-jetbrains"


@pytest.mark.asyncio
async def test_retrieves_nothing_when_current_version_is_invalid() -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="1.0.1")
    )

    update = await get_update_if_available(
        version_update_notifier,
        current_version="invalid-version",
        update_cache_repository=FakeUpdateCacheRepository(),
    )

    assert update is None


@pytest.mark.parametrize(
    ("cause", "expected_message_substring"),
    [
        (VersionUpdateGatewayCause.TOO_MANY_REQUESTS, "Rate limit exceeded"),
        (VersionUpdateGatewayCause.INVALID_RESPONSE, "invalid response"),
        (
            VersionUpdateGatewayCause.NOT_FOUND,
            "Unable to fetch the releases. Please check your permissions.",
        ),
        (VersionUpdateGatewayCause.ERROR_RESPONSE, "Unexpected response"),
        (VersionUpdateGatewayCause.REQUEST_FAILED, "Network error"),
    ],
)
@pytest.mark.asyncio
async def test_raises_version_update_error(
    cause: VersionUpdateGatewayCause, expected_message_substring: str
) -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        error=VersionUpdateGatewayError(cause=cause)
    )

    with pytest.raises(VersionUpdateError) as excinfo:
        await get_update_if_available(
            version_update_notifier,
            current_version="1.0.0",
            update_cache_repository=FakeUpdateCacheRepository(),
        )

    assert expected_message_substring in str(excinfo.value)


@pytest.mark.asyncio
async def test_notifies_and_updates_cache_when_repository_is_empty(
    current_timestamp: int,
) -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="1.0.1")
    )
    update_cache_repository = FakeUpdateCacheRepository()

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=update_cache_repository,
        get_current_timestamp=lambda: current_timestamp,
    )

    assert update is not None
    assert update.latest_version == "1.0.1"
    assert update.should_notify is True
    assert version_update_notifier.fetch_update_calls == 1
    assert update_cache_repository.update_cache is not None
    assert update_cache_repository.update_cache.latest_version == "1.0.1"
    assert update_cache_repository.update_cache.stored_at_timestamp == current_timestamp


@pytest.mark.asyncio
async def test_does_not_notify_when_an_available_update_has_been_recently_cached(
    current_timestamp: int,
) -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="1.0.1")
    )
    timestamp_twelve_hours_ago = current_timestamp - 12 * 60 * 60
    update_cache = UpdateCache(
        latest_version="1.0.1", stored_at_timestamp=timestamp_twelve_hours_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=update_cache_repository,
        get_current_timestamp=lambda: current_timestamp,
    )

    assert update is not None
    assert update.latest_version == "1.0.1"
    assert update.should_notify is False
    assert version_update_notifier.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_retrieves_nothing_when_the_recently_cached_update_is_the_one_currently_in_use(
    current_timestamp: int,
) -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="1.0.1")
    )
    timestamp_twelve_hours_ago = current_timestamp - 12 * 60 * 60
    update_cache = UpdateCache(
        latest_version="1.0.1", stored_at_timestamp=timestamp_twelve_hours_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.1",
        update_cache_repository=update_cache_repository,
        get_current_timestamp=lambda: current_timestamp,
    )

    assert update is None
    assert version_update_notifier.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_retrieves_fresh_update_and_notifies_and_updates_cache_when_cache_is_not_fresh(
    current_timestamp: int,
) -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="1.0.2")
    )
    timestamp_two_days_ago = current_timestamp - 48 * 60 * 60
    update_cache = UpdateCache(
        latest_version="1.0.1", stored_at_timestamp=timestamp_two_days_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=update_cache_repository,
        get_current_timestamp=lambda: current_timestamp,
    )

    assert update is not None
    assert version_update_notifier.fetch_update_calls == 1
    assert update.should_notify is True
    assert update.latest_version == "1.0.2"
    assert update_cache_repository.update_cache is not None
    assert update_cache_repository.update_cache.stored_at_timestamp == current_timestamp
    assert update_cache_repository.update_cache.latest_version == "1.0.2"


@pytest.mark.asyncio
async def test_updates_cache_timestamp_with_current_version_when_no_update_is_available(
    current_timestamp: int,
) -> None:
    version_update_notifier = FakeVersionUpdateGateway(update=None)
    timestamp_two_days_ago = current_timestamp - 48 * 60 * 60
    update_cache = UpdateCache(
        latest_version="1.0.0", stored_at_timestamp=timestamp_two_days_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)

    update = await get_update_if_available(
        version_update_notifier,
        current_version="1.0.0",
        update_cache_repository=update_cache_repository,
        get_current_timestamp=lambda: current_timestamp,
    )

    assert update is None
    assert version_update_notifier.fetch_update_calls == 1
    assert update_cache_repository.update_cache is not None
    assert update_cache_repository.update_cache.latest_version == "1.0.0"
    assert update_cache_repository.update_cache.stored_at_timestamp == current_timestamp


@pytest.mark.asyncio
async def test_updates_cache_timestamp_with_current_version_when_gateway_errors(
    current_timestamp: int,
) -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        error=VersionUpdateGatewayError(cause=VersionUpdateGatewayCause.ERROR_RESPONSE)
    )
    timestamp_two_days_ago = current_timestamp - 48 * 60 * 60
    update_cache = UpdateCache(
        latest_version="1.0.0", stored_at_timestamp=timestamp_two_days_ago
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)

    with pytest.raises(VersionUpdateError):
        await get_update_if_available(
            version_update_notifier,
            current_version="1.0.0",
            update_cache_repository=update_cache_repository,
            get_current_timestamp=lambda: current_timestamp,
        )

    assert version_update_notifier.fetch_update_calls == 1
    assert update_cache_repository.update_cache is not None
    assert update_cache_repository.update_cache.latest_version == "1.0.0"
    assert update_cache_repository.update_cache.stored_at_timestamp == current_timestamp
