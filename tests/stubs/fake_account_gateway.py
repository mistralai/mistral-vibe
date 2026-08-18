from __future__ import annotations

from vibe.app_server._account import (
    AccountGatewayUnauthorized,
    AccountGatewayUnavailable,
    WhoAmIResult,
)


class FakeAccountGateway:
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
        self, *, base_url: str, api_key: str, timeout: float | None = None
    ) -> WhoAmIResult:
        self.calls.append((base_url, api_key))
        if self.unauthorized:
            raise AccountGatewayUnauthorized()
        if self.unavailable:
            raise AccountGatewayUnavailable()
        if self.result is None:
            raise RuntimeError("FakeAccountGateway requires a result")
        return self.result
