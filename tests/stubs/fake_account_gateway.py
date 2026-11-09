from __future__ import annotations

from vibe.app_server._account import (
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    WhoAmIResult,
)
from vibe.core.platform_auth import (
    PlatformCredentialProvider,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)


class FakeAccountGateway:
    """Records ``(base_url, Authorization)``: what a real gateway would send."""

    def __init__(
        self,
        result: WhoAmIResult | None = None,
        *,
        unauthorized: bool = False,
        unavailable: bool = False,
    ) -> None:
        self.result = result
        self.unauthorized = unauthorized
        self.unavailable = unavailable
        self.calls: list[tuple[str, str]] = []

    async def read(
        self,
        *,
        base_url: str,
        credentials: PlatformCredentialProvider,
        timeout: float | None = None,
    ) -> WhoAmIResult:
        resolved = await credentials.resolve(PlatformCredentialTarget(base_url))
        bearer = (
            resolved.headers.get("Authorization", "").removeprefix("Bearer ")
            if isinstance(resolved, PlatformCredentialSnapshot)
            else ""
        )
        self.calls.append((base_url, bearer))
        if self.unauthorized:
            raise AccountGatewayUnauthorized()
        if self.unavailable:
            raise AccountGatewayUnavailable()
        if self.result is None:
            raise RuntimeError("FakeAccountGateway requires a result")
        return self.result
