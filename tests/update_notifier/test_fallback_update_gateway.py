from __future__ import annotations

import pytest

from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe.cli.update_notifier.adapters.fallback_update_gateway import (
    FallbackUpdateGateway,
)
from vibe.cli.update_notifier.ports.update_gateway import (
    Update,
    UpdateGatewayCause,
    UpdateGatewayError,
    UpdateGatewayUnavailableError,
)


@pytest.mark.asyncio
async def test_uses_primary_answer_without_querying_fallback() -> None:
    fallback = FakeUpdateGateway(update=Update(latest_version="2.0.0"))
    gateway = FallbackUpdateGateway(primary=FakeUpdateGateway(), fallback=fallback)

    update = await gateway.fetch_update()

    assert update is None
    assert fallback.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_uses_fallback_when_primary_is_unavailable() -> None:
    primary = FakeUpdateGateway(
        error=UpdateGatewayUnavailableError(cause=UpdateGatewayCause.ERROR_RESPONSE)
    )
    fallback = FakeUpdateGateway(update=Update(latest_version="2.0.0"))
    gateway = FallbackUpdateGateway(primary=primary, fallback=fallback)

    update = await gateway.fetch_update()

    assert update == Update(latest_version="2.0.0")


@pytest.mark.asyncio
async def test_propagates_primary_failure_without_querying_fallback() -> None:
    primary = FakeUpdateGateway(
        error=UpdateGatewayError(cause=UpdateGatewayCause.ERROR_RESPONSE)
    )
    fallback = FakeUpdateGateway(update=Update(latest_version="2.0.0"))
    gateway = FallbackUpdateGateway(primary=primary, fallback=fallback)

    with pytest.raises(UpdateGatewayError) as excinfo:
        await gateway.fetch_update()

    assert excinfo.value.cause == UpdateGatewayCause.ERROR_RESPONSE
    assert fallback.fetch_update_calls == 0


@pytest.mark.asyncio
async def test_propagates_fallback_failure() -> None:
    primary = FakeUpdateGateway(
        error=UpdateGatewayUnavailableError(cause=UpdateGatewayCause.ERROR_RESPONSE)
    )
    fallback = FakeUpdateGateway(
        error=UpdateGatewayError(cause=UpdateGatewayCause.REQUEST_FAILED)
    )
    gateway = FallbackUpdateGateway(primary=primary, fallback=fallback)

    with pytest.raises(UpdateGatewayError) as excinfo:
        await gateway.fetch_update()

    assert excinfo.value.cause == UpdateGatewayCause.REQUEST_FAILED
