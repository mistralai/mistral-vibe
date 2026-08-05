from __future__ import annotations

from vibe.app_server._identity import (
    IdentityGatewayUnauthorized,
    IdentityGatewayUnavailable,
    IdentityResult,
)


class FakeIdentityGateway:
    def __init__(
        self,
        result: IdentityResult | None = None,
        *,
        unauthorized: bool = False,
        unavailable: bool = False,
    ) -> None:
        self.result = result
        self.unauthorized = unauthorized
        self.unavailable = unavailable
        self.calls: list[tuple[str, str]] = []

    async def read(
        self, *, base_url: str, api_key: str, timeout: float | None = None
    ) -> IdentityResult:
        self.calls.append((base_url, api_key))
        if self.unauthorized:
            raise IdentityGatewayUnauthorized()
        if self.unavailable:
            raise IdentityGatewayUnavailable()
        if self.result is None:
            raise RuntimeError("FakeIdentityGateway requires a result")
        return self.result
