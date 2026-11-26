from __future__ import annotations

from vibe.app_server._billing import BillingRead
from vibe.core.platform_auth import PlatformCredentialProvider


class FakeBillingGateway:
    def __init__(self, result: BillingRead | None = None) -> None:
        self.result = result or BillingRead()
        self.calls: list[tuple[str, PlatformCredentialProvider, bool]] = []

    async def read(
        self,
        *,
        api_base: str,
        credentials: PlatformCredentialProvider,
        with_budget: bool = True,
    ) -> BillingRead:
        self.calls.append((api_base, credentials, with_budget))
        if not with_budget:
            return BillingRead(subscription=self.result.subscription)
        return self.result
