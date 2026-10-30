from __future__ import annotations

from typing import Any, Protocol

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.core.platform_auth import (
    MistralPlatformHTTPClient,
    PlatformCredentialProvider,
    PlatformCredentialUnavailable,
)
from vibe.observability.logging import logger

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
        self,
        *,
        base_url: str,
        credentials: PlatformCredentialProvider,
        timeout: float | None = None,
    ) -> IdentityResult: ...


class HttpIdentityGateway:
    async def read(
        self,
        *,
        base_url: str,
        credentials: PlatformCredentialProvider,
        timeout: float | None = None,
    ) -> IdentityResult:
        client_kwargs: dict[str, Any] = {}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        try:
            client = MistralPlatformHTTPClient(
                base_url=base_url, credentials=credentials, **client_kwargs
            )
        except ValueError as exc:
            raise IdentityGatewayUnavailable("Invalid platform base URL") from exc
        try:
            async with client:
                response = await client.get(_IDENTITY_PATH.lstrip("/"))
        except httpx2.RequestError as exc:
            raise IdentityGatewayUnavailable() from exc

        if response.status_code in {httpx2.codes.UNAUTHORIZED, httpx2.codes.FORBIDDEN}:
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
    credentials: PlatformCredentialProvider,
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
        return await gateway.read(
            base_url=base_url, credentials=credentials, timeout=timeout
        )
    except IdentityGatewayUnauthorized:
        logger.info("Identity endpoint rejected the platform credential (401/403).")
        return None
    except PlatformCredentialUnavailable as exc:
        logger.info("Identity endpoint credential is %s.", exc.required.reason)
        return None
    except IdentityGatewayUnavailable as exc:
        logger.warning(
            "Failed to fetch identity (%s)", type(exc).__name__, exc_info=exc
        )
        return None
