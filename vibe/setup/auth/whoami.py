"""Whoami / tenant-domain discovery primitives.

Lives under ``setup/auth`` (not ``app_server``) because onboarding and the ACP
auth controller both need it before an app server exists. ``AccountController``
in ``vibe.app_server._account`` re-imports these types for its runtime plan
lookups.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Final, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from vibe.app_server.models import AccountPlanKind
from vibe.core.config import ProviderConfig
from vibe.core.paths import WHOAMI_CACHE_FILE
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

_WHOAMI_PATH = "/api/vibe/whoami"

# Sentinel for plan fields (user_plan, planName, planType) when there is no
# Mistral provider configured at all — the user is not one of ours, so there
# is nothing to look up. This is an *expected* absence.
#
# It is deliberately distinct from ``None``: ``None`` is reserved for the case
# where a Mistral credential exists and we tried to populate the fields but the
# lookup failed (missing key / whoami timeout / network / unmapped plan). In
# other words ``None`` signals a problem in *our* system worth alerting on,
# whereas ``NO_PLAN_DATA`` is the benign "not our user" state.
#
# Note: whether the active model is Mistral is NOT what gates this — a user on
# a third-party model who still has a Mistral provider configured gets their
# real plan. Only the render layer gates on the active backend.
NO_PLAN_DATA = "NO_PLAN_DATA"


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


_WHOAMI_CACHE_TTL_SECONDS: Final = 6 * 60 * 60


def load_cached_whoami(api_key: str) -> WhoAmIResult | None:
    """Return the cached /whoami result for ``api_key`` if still fresh, else None.

    The cache is user-scoped (keyed by the hashed Mistral key) and shared across
    sessions/processes, so plan/org/customer data is neither re-fetched on every
    launch nor duplicated into each session's ``meta.json``. Fails open: any
    read/parse error, missing entry, or stale entry yields None so the caller
    refetches.
    """
    entry = _read_whoami_entries().get(_whoami_cache_key(api_key))
    if not isinstance(entry, dict):
        return None
    stored_at = entry.get("stored_at_timestamp")
    payload = entry.get("payload")
    if not isinstance(stored_at, int) or not isinstance(payload, dict):
        return None
    if stored_at <= int(time.time()) - _WHOAMI_CACHE_TTL_SECONDS:
        return None
    try:
        return WhoAmIResult.model_validate(payload)
    except (ValueError, ValidationError):
        return None


def store_cached_whoami(api_key: str, result: WhoAmIResult) -> None:
    """Persist a /whoami result under the hashed ``api_key``. Best-effort."""
    entries = _read_whoami_entries()
    entries[_whoami_cache_key(api_key)] = {
        "stored_at_timestamp": int(time.time()),
        "payload": result.model_dump(mode="json"),
    }
    _write_whoami_entries(entries)


def clear_cached_whoami(api_key: str) -> None:
    """Drop the cached /whoami entry for ``api_key`` (e.g. after a 401/403).

    A rejected credential means any cached plan is untrustworthy, so it must not
    keep being served for the TTL. Best-effort; no-op when nothing is cached.
    """
    entries = _read_whoami_entries()
    if entries.pop(_whoami_cache_key(api_key), None) is not None:
        _write_whoami_entries(entries)


def _whoami_cache_key(api_key: str) -> str:
    # Reuse the experiment cache's hashing so the on-disk key is anonymous and
    # consistent with the rest of the telemetry stack. Imported lazily to keep
    # this setup-layer module decoupled from the experiments engine at import
    # time.
    from vibe.core.experiments.manager import hash_api_key

    return hash_api_key(api_key)


def _read_whoami_entries() -> dict[str, Any]:
    try:
        with WHOAMI_CACHE_FILE.path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_whoami_entries(entries: dict[str, Any]) -> None:
    cache_path = WHOAMI_CACHE_FILE.path
    tmp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(entries, f, separators=(",", ":"))
        os.replace(tmp_path, cache_path)
    except (OSError, TypeError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.debug("Failed to write whoami cache file %s", cache_path, exc_info=True)


class WhoAmICache:
    """In-memory /whoami cache backed by a cross-session on-disk cache.

    Fetches once per ``(base_url, api_key)`` pair and caches successes so
    experiment init and ``AccountController`` share a single round-trip. On an
    in-memory miss it consults the user-scoped on-disk cache
    (:func:`load_cached_whoami`) before hitting the network, and persists every
    network success back to disk. Failures are not cached so a later caller can
    retry.

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

    def invalidate(self, api_key: str) -> None:
        """Evict every in-memory entry for ``api_key`` and drop its disk entry.

        Called after a rejected credential (401/403) so neither this process nor
        a later session serves a stale plan for the key.
        """
        for key in [k for k in self._cache if k[1] == api_key]:
            del self._cache[key]
        clear_cached_whoami(api_key)

    async def resolve(
        self,
        *,
        base_url: str,
        api_key: str,
        gateway: AccountGateway | None = None,
        timeout: float | None = None,
    ) -> WhoAmIResult | None:
        """Fetch /whoami once per ``(base_url, api_key)`` and cache successes.

        Read-through: an in-memory hit is returned first, then the cross-session
        on-disk cache (:func:`load_cached_whoami`); only on a miss do we hit the
        network, persisting a success to both caches. Single-flight so
        concurrent callers coalesce onto one request; failures are not cached so
        a later caller can retry.
        """
        key = (base_url, api_key)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            disk_cached = load_cached_whoami(api_key)
            if disk_cached is not None:
                self._cache[key] = disk_cached
                return disk_cached
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
            store_cached_whoami(api_key, result)
            return result


def derive_user_plan(result: WhoAmIResult | None) -> str | None:
    """Map a /whoami result to the legacy ``user_plan`` display label.

    Pure function shared by the account controller and the experiments init
    path so telemetry's ``user_plan`` is populated before the early
    ``vibe.new_session`` / ``vibe.ready`` events and on surfaces that never
    call ``account/read`` (ACP, programmatic). Returns ``None`` when whoami
    is unavailable or the plan is not in the known mapping.
    """
    if result is None:
        return None
    kind = result.plan_type
    name = result.plan_name.strip().upper()
    match kind:
        case AccountPlanKind.CHAT:
            return {
                "FREE": "Free",
                "INDIVIDUAL": "Pro",
                "EDU": "Student",
                "TEAM": "Team",
            }.get(name)
        case AccountPlanKind.API:
            if not name:
                return None
            return "Free API" if "FREE" in name else "PAYG API"
        case AccountPlanKind.MISTRAL_CODE:
            return {"F": "Free Codestral", "E": "Code Enterprise"}.get(name)
        case _:
            return None


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
