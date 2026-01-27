from __future__ import annotations

from vibe.cli.plan_offer.ports.whoami_gateway import (
    WhoAmIGatewayError,
    WhoAmIGatewayUnauthorized,
    WhoAmIResponse,
)


class FakeWhoAmIGateway:
    def __init__(
        self,
        response: WhoAmIResponse | None = None,
        *,
        unauthorized: bool = False,
        error: bool = False,
    ) -> None:
        self._response = response
        self._unauthorized = unauthorized
        self._error = error
        self.calls: list[str] = []

    async def whoami(self, api_key: str) -> WhoAmIResponse:
        self.calls.append(api_key)
        if self._unauthorized:
            raise WhoAmIGatewayUnauthorized()
        if self._error:
            raise WhoAmIGatewayError()
        if self._response is None:
            msg = "FakeWhoAmIGateway requires a response when no error is set."
            raise RuntimeError(msg)
        return self._response
