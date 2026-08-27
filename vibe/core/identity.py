from __future__ import annotations

from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

_IDENTITY_PATH = "/users/me"


class _IdentityEntity(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    name: str


class IdentityResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    workspace: _IdentityEntity | None = None
    organization: _IdentityEntity | None = None


class IdentityGatewayUnauthorized(Exception):
    pass


class IdentityGatewayUnavailable(Exception):
    pass


class IdentityGateway(Protocol):
    async def read(
        self, *, base_url: str, api_key: str, timeout: float | None = None
    ) -> IdentityResult: ...


class HttpIdentityGateway:
    async def read(
        self, *, base_url: str, api_key: str, timeout: float | None = None
    ) -> IdentityResult:
        url = f"{base_url.rstrip('/')}{_IDENTITY_PATH}"
        client_kwargs: dict[str, object] = {"verify": build_ssl_context()}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        try:
            async with VibeAsyncHTTPClient(**client_kwargs) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {api_key}"}
                )
        except httpx.RequestError as exc:
            raise IdentityGatewayUnavailable() from exc

        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise IdentityGatewayUnauthorized()
        if not response.is_success:
            raise IdentityGatewayUnavailable(
                f"Unexpected identity response status {response.status_code}"
            )

        try:
            return IdentityResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise IdentityGatewayUnavailable("Invalid identity response") from exc


async def fetch_identity(
    *,
    base_url: str,
    api_key: str,
    gateway: IdentityGateway | None = None,
    timeout: float | None = None,
) -> IdentityResult | None:
    """Fetch the caller's identity from the Mistral API, failing open.

    Returns ``None`` on any failure (unauthorized, unavailable, network, invalid
    payload) so non-critical callers (experiments, telemetry) never break when
    identity is unavailable. Mirrors the None-on-error handling in
    ``IdentityController._read``.
    """
    gateway = gateway or HttpIdentityGateway()
    try:
        return await gateway.read(base_url=base_url, api_key=api_key, timeout=timeout)
    except IdentityGatewayUnauthorized:
        logger.info(
            "Identity endpoint rejected the API key (401/403); "
            "the key may be invalid or expired."
        )
        return None
    except IdentityGatewayUnavailable as exc:
        logger.warning(
            "Failed to fetch identity (%s)", type(exc).__name__, exc_info=exc
        )
        return None
