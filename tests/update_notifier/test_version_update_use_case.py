from __future__ import annotations

import pytest

from vibe.cli.update_notifier.fake_version_update_gateway import (
    FakeVersionUpdateGateway,
)
from vibe.cli.update_notifier.version_update import (
    VersionUpdateError,
    is_version_update_available,
)
from vibe.cli.update_notifier.version_update_gateway import (
    VersionUpdate,
    VersionUpdateGatewayCause,
    VersionUpdateGatewayError,
)


@pytest.mark.asyncio
async def test_retrieves_the_latest_version_update_when_available() -> None:
    latest_update = "1.0.3"
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version=latest_update)
    )

    update = await is_version_update_available(
        version_update_notifier, current_version="1.0.0"
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

    update = await is_version_update_available(
        version_update_notifier, current_version=current_version
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

    update = await is_version_update_available(
        version_update_notifier, current_version=current_version
    )

    assert update is None


@pytest.mark.asyncio
async def test_retrieves_nothing_when_no_version_is_available() -> None:
    version_update_notifier = FakeVersionUpdateGateway(update=None)

    update = await is_version_update_available(
        version_update_notifier, current_version="1.0.0"
    )

    assert update is None


@pytest.mark.asyncio
async def test_retrieves_nothing_when_latest_version_is_invalid() -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="invalid-version")
    )

    update = await is_version_update_available(
        version_update_notifier, current_version="1.0.0"
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

    update = await is_version_update_available(
        version_update_notifier, current_version="1.0.0"
    )

    assert update is not None
    assert update.latest_version == "1.6.1-jetbrains"


@pytest.mark.asyncio
async def test_retrieves_nothing_when_current_version_is_invalid() -> None:
    version_update_notifier = FakeVersionUpdateGateway(
        update=VersionUpdate(latest_version="1.0.1")
    )

    update = await is_version_update_available(
        version_update_notifier, current_version="invalid-version"
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
        await is_version_update_available(
            version_update_notifier, current_version="1.0.0"
        )

    assert expected_message_substring in str(excinfo.value)
