from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import httpx

from vibe.cli.plan_offer.ports.whoami_gateway import (
    WhoAmIGatewayError,
    WhoAmIGatewayUnauthorized,
    WhoAmIResponse,
)

BASE_URL = "https://console.mistral.ai"
WHOAMI_PATH = "/api/vibe/whoami"


class HttpWhoAmIGateway:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    async def whoami(self, api_key: str) -> WhoAmIResponse:
        url = f"{self._base_url}{WHOAMI_PATH}"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            raise WhoAmIGatewayError() from exc

        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise WhoAmIGatewayUnauthorized()
        if not response.is_success:
            raise WhoAmIGatewayError(f"Unexpected status {response.status_code}")

        payload = _safe_json(response) or {}
        return WhoAmIResponse(
            is_pro_plan=_parse_bool(payload.get("is_pro_plan")),
            advertise_pro_plan=_parse_bool(payload.get("advertise_pro_plan")),
            prompt_switching_to_pro_plan=_parse_bool(
                payload.get("prompt_switching_to_pro_plan")
            ),
        )


def _safe_json(response: httpx.Response) -> Mapping[str, object] | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return cast(Mapping[str, object], data) if isinstance(data, dict) else None


def _parse_bool(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        match value.strip().lower():
            case "true":
                return True
            case "false":
                return False
            case _:
                raise WhoAmIGatewayError("Invalid boolean string in whoami response")
    raise WhoAmIGatewayError("Invalid boolean value in whoami response")
