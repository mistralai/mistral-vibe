"""Whoami / tenant-domain discovery primitives.

Lives under ``setup/auth`` (not ``app_server``) because onboarding and the ACP
auth controller both need it before an app server exists. ``AccountController``
in ``vibe.app_server._account`` re-imports these types for its runtime plan
lookups.
"""

from __future__ import annotations

import asyncio
from typing import Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from vibe.app_server.models import AccountPlanKind
from vibe.core.config import ProviderConfig
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

_WHOAMI_PATH = "/api/vibe/whoami"


class WhoAmIResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    plan_type: AccountPlanKind
    plan_name: str
    prompt_switching_to_pro_plan: bool = False
    organization_kind: str | None = None
    customer_id: str | None = None
    api_base: str | None = None
    vibe_base: str | None = None

    @field_validator("plan_type", mode="before")
    @classmethod
    def parse_plan_type(cls, value: object) -> AccountPlanKind:
        if not isinstance(value, str):
            raise ValueError("plan_type must be a string")
        try:
            return AccountPlanKind(value.strip().lower())
        except ValueError as exc:
            raise ValueError(f"Unsupported plan_type: {value}") from exc


class AccountGatewayUnauthorized(Exception):
    pass


class AccountGatewayUnavailable(Exception):
    pass


class AccountGateway(Protocol):
    async def read(
        self, *, base_url: str, api_key: str, timeout: float | None = None
    ) -> WhoAmIResult: ...


class HttpAccountGateway:
    async def read(
        self, *, base_url: str, api_key: str, timeout: float | None = None
    ) -> WhoAmIResult:
        url = f"{base_url.rstrip('/')}{_WHOAMI_PATH}"
        client_kwargs: dict[str, object] = {"verify": build_ssl_context()}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        try:
            async with VibeAsyncHTTPClient(**client_kwargs) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {api_key}"}
                )
        except httpx.RequestError as exc:
            raise AccountGatewayUnavailable() from exc

        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise AccountGatewayUnauthorized()
        if not response.is_success:
            raise AccountGatewayUnavailable(
                f"Unexpected account response status {response.status_code}"
            )

        try:
            return WhoAmIResult.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise AccountGatewayUnavailable("Invalid account response") from exc


async def fetch_whoami(
    base_url: str, api_key: str, *, timeout: float | None = None
) -> WhoAmIResult | None:
    """Fetch /whoami without raising — returns None on any failure.

    Intended for onboarding-time discovery where a missing/misbehaving endpoint
    should degrade gracefully rather than block the sign-in.
    """
    try:
        return await HttpAccountGateway().read(
            base_url=base_url, api_key=api_key, timeout=timeout
        )
    except (AccountGatewayUnauthorized, AccountGatewayUnavailable) as exc:
        logger.info("Failed to fetch /whoami (%s), skipping", type(exc).__name__)
        return None


class WhoAmICache:
    """Session-scoped cache for /whoami results.

    Fetches once per ``(base_url, api_key)`` pair and caches successes so
    experiment init and ``AccountController`` share a single round-trip.
    Failures are not cached so a later caller can retry.

    Use :meth:`resolve` to fetch-or-return-cached.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: dict[tuple[str, str], WhoAmIResult] = {}

    def peek(self, *, base_url: str, api_key: str) -> WhoAmIResult | None:
        """Return an already-fetched result for reuse, without fetching."""
        return self._cache.get((base_url, api_key))

    def populate(self, *, base_url: str, api_key: str, result: WhoAmIResult) -> None:
        """Store a known result without fetching, for callers that already have one."""
        self._cache[(base_url, api_key)] = result

    async def resolve(
        self,
        *,
        base_url: str,
        api_key: str,
        gateway: AccountGateway | None = None,
        timeout: float | None = None,
    ) -> WhoAmIResult | None:
        """Fetch /whoami once per ``(base_url, api_key)`` and cache successes.

        Single-flight so concurrent callers coalesce onto one request; failures
        are not cached so a later caller can retry.
        """
        key = (base_url, api_key)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            gw = gateway or HttpAccountGateway()
            try:
                result = await gw.read(
                    base_url=base_url, api_key=api_key, timeout=timeout
                )
            except (AccountGatewayUnauthorized, AccountGatewayUnavailable) as exc:
                logger.info(
                    "Failed to fetch /whoami for cache (%s), skipping",
                    type(exc).__name__,
                )
                return None
            self._cache[key] = result
            return result


def _sanitize_tenant_url(candidate: str, *, field: str) -> str | None:
    """Trust boundary: the tenant's /whoami dictates future API/chat traffic
    destinations. Reject anything not clearly an HTTPS origin so a compromised
    or misconfigured console cannot downgrade traffic or point it at a
    lookalike path (e.g. ``https://good.corp/../evil.corp``).
    """
    stripped = candidate.rstrip("/")
    try:
        parsed = urlparse(stripped)
    except ValueError:
        logger.warning("Rejecting tenant %s URL: unparsable value %r", field, candidate)
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        logger.warning(
            "Rejecting tenant %s URL: expected https origin, got %r", field, candidate
        )
        return None
    if ".." in parsed.path:
        logger.warning(
            "Rejecting tenant %s URL: path contains '..' (%r)", field, candidate
        )
        return None
    return stripped


async def resolve_tenant_domains(
    provider: ProviderConfig,
    console_base_url: str,
    api_key: str,
    current_vibe_base_url: str,
) -> tuple[ProviderConfig, str]:
    """Fetch /whoami and return ``(provider, vibe_base_url)`` updated with any
    tenant-advertised domains.

    Callers pass in the current values and receive replacements only for the
    fields whoami declared. When the fetch fails or the response omits
    ``api_base``/``vibe_base``, the inputs are returned unchanged.
    """
    whoami = await fetch_whoami(console_base_url, api_key)
    if whoami is None or (whoami.api_base is None and whoami.vibe_base is None):
        return provider, current_vibe_base_url
    vibe_base_url = current_vibe_base_url
    if whoami.api_base:
        sanitized = _sanitize_tenant_url(whoami.api_base, field="api")
        if sanitized is not None:
            provider = provider.model_copy(update={"api_base": f"{sanitized}/v1"})
    if whoami.vibe_base:
        sanitized = _sanitize_tenant_url(whoami.vibe_base, field="vibe_base")
        if sanitized is not None:
            vibe_base_url = sanitized
    return provider, vibe_base_url
