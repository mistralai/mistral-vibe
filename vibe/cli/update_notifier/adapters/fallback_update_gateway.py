from __future__ import annotations

from vibe.cli.update_notifier.ports.update_gateway import (
    Update,
    UpdateGateway,
    UpdateGatewayUnavailableError,
)
from vibe.observability.logging import logger


class FallbackUpdateGateway(UpdateGateway):
    def __init__(self, primary: UpdateGateway, fallback: UpdateGateway) -> None:
        self._primary = primary
        self._fallback = fallback

    async def fetch_update(self) -> Update | None:
        try:
            return await self._primary.fetch_update()
        except UpdateGatewayUnavailableError as exc:
            logger.info("Primary update source unavailable, using fallback: %s", exc)
            return await self._fallback.fetch_update()
