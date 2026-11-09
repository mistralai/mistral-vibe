from __future__ import annotations

from vibe.app_server._identity import (
    IdentityGatewayUnauthorized,
    IdentityGatewayUnavailable,
    IdentityResult,
)
from vibe.core.platform_auth import (
    PlatformCredentialProvider,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)


class FakeIdentityGateway:
    def __init__(
        self,
        result: IdentityResult | None = None,
        *,
        unauthorized: bool = False,
        unavailable: bool = False,
        reject_credentials: bool = False,
    ) -> None:
        self.result = result
        self.unauthorized = unauthorized
        self.unavailable = unavailable
        self.reject_credentials = reject_credentials
        self.calls: list[str] = []

    async def read(
        self,
        *,
        base_url: str,
        credentials: PlatformCredentialProvider,
        timeout: float | None = None,
    ) -> IdentityResult:
        del timeout
        self.calls.append(base_url)
        if self.unauthorized:
            if self.reject_credentials:
                target = PlatformCredentialTarget(base_url=base_url)
                resolved = await credentials.resolve(target)
                if isinstance(resolved, PlatformCredentialSnapshot):
                    await credentials.reject(
                        target, observed=resolved.identity, reason="http_unauthorized"
                    )
            raise IdentityGatewayUnauthorized()
        if self.unavailable:
            raise IdentityGatewayUnavailable()
        if self.result is None:
            raise RuntimeError("FakeIdentityGateway requires a result")
        return self.result
