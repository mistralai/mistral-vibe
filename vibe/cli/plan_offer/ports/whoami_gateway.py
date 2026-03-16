from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import TypeAdapter, ValidationError


class WhoAmIPlanType(StrEnum):
    API = "API"
    CHAT = "CHAT"
    MISTRAL_CODE = "MISTRAL_CODE"
    UNKNOWN = "UNKNOWN"
    UNAUTHORIZED = "UNAUTHORIZED"

    @classmethod
    def from_string(cls, value: str) -> WhoAmIPlanType:
        try:
            return cls(value.strip().upper())
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class WhoAmIResponse:
    plan_type: WhoAmIPlanType
    plan_name: str
    prompt_switching_to_pro_plan: bool

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> WhoAmIResponse:
        plan_type = payload.get("plan_type")
        plan_name = payload.get("plan_name")
        if not isinstance(plan_type, str) or not isinstance(plan_name, str):
            raise WhoAmIGatewayError(f"Invalid whoami response: {payload}")
        return cls(
            plan_type=WhoAmIPlanType.from_string(plan_type),
            plan_name=plan_name.strip(),
            prompt_switching_to_pro_plan=_parse_bool(
                payload.get("prompt_switching_to_pro_plan")
            ),
        )


def _parse_bool(value: object | None) -> bool:
    if value is None:
        return False
    try:
        return TypeAdapter(bool).validate_python(value)
    except ValidationError as e:
        raise WhoAmIGatewayError(
            f"Invalid boolean value in whoami response: {value}"
        ) from e


class WhoAmIGatewayUnauthorized(Exception):
    pass


class WhoAmIGatewayError(Exception):
    pass


class WhoAmIGateway(Protocol):
    async def whoami(self, api_key: str) -> WhoAmIResponse: ...
