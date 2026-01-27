from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class WhoAmIResponse:
    is_pro_plan: bool
    advertise_pro_plan: bool
    prompt_switching_to_pro_plan: bool


class WhoAmIGatewayUnauthorized(Exception):
    pass


class WhoAmIGatewayError(Exception):
    pass


class WhoAmIGateway(Protocol):
    async def whoami(self, api_key: str) -> WhoAmIResponse: ...
