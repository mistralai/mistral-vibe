"""The org config, held and refreshed once per OS process.

A session is built from the copy the service holds and never waits for the
network, except with nothing held yet (first run, a new API key), and then for
at most ``MANAGED_CONFIG_FETCH_BUDGET``. The copy is refreshed in the
background once older than ``ORG_CONFIG_REFRESH_AFTER_S``; each open session
follows it (``followed``) and loads a change at its next turn boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
import tomllib
from typing import Final

from vibe.app_server._admin_config import MANAGED_CONFIG_FETCH_BUDGET
from vibe.core.config import DEFAULT_VIBE_BASE_URL, VibeConfigSchema
from vibe.core.config.admin_config import (
    AdminConfigApplyResult,
    AdminConfigOutcome,
    ManagedConfigResult,
    fetch_managed_config,
)
from vibe.core.config.managed_config_cache import (
    MANAGED_CONFIG_CACHE_TTL_SECONDS,
    load_cached_managed_config_record,
    managed_config_cache_key,
    store_cached_managed_config,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.telemetry.send import credential_cache_key_is_current
from vibe.core.utils.remote_cache import (
    Cached,
    CacheStore,
    Followed,
    RefreshPolicy,
    RemoteCache,
    RemoteFetchFailedError,
)
from vibe.utils.api_keys import resolve_api_key

ORG_CONFIG_REFRESH_AFTER_S: Final = 10 * 60

ORG_CONFIG_POLICY: Final = RefreshPolicy(
    refresh_after_s=ORG_CONFIG_REFRESH_AFTER_S,
    max_age_s=MANAGED_CONFIG_CACHE_TTL_SECONDS,
    retry_after_s=30,
    max_retry_after_s=10 * 60,
    fetch_timeout_s=MANAGED_CONFIG_FETCH_BUDGET,
)

FetchManagedConfig = Callable[[str, str], Awaitable[ManagedConfigResult]]


@dataclass(frozen=True, slots=True)
class OrgConfigKey:
    """One user's org config: the hashed API key it is cached under, on the
    endpoint it is fetched from.

    The endpoint is part of the key: account reconciliation can move it
    mid-process, and the policy held for the old one is not the new one's.
    The raw API key is carried for the fetch, never compared.
    """

    cache_key: str
    base_url: str
    api_key: str = field(compare=False, repr=False)


class OrgConfigFetchError(Exception):
    def __init__(self, outcome: AdminConfigOutcome, error: str | None) -> None:
        super().__init__(error or outcome.value)
        self.outcome = outcome


class DiskOrgConfigStore:
    """The 7-day disk copy, shared with other Vibe processes."""

    def load(self, key: OrgConfigKey) -> Cached[str] | None:
        record = load_cached_managed_config_record(_disk_key(key))
        if record is None:
            return None
        toml_text, stored_at = record
        return Cached(toml_text, stored_at=stored_at)

    def save(self, key: OrgConfigKey, entry: Cached[str]) -> None:
        if not credential_cache_key_is_current(key.cache_key):
            return
        store_cached_managed_config(
            _disk_key(key), entry.value, stored_at=int(entry.stored_at)
        )


def _disk_key(key: OrgConfigKey) -> str:
    # The default endpoint keeps the bare key that existing caches hold.
    base_url = key.base_url.rstrip("/")
    if base_url == DEFAULT_VIBE_BASE_URL:
        return key.cache_key
    return f"{key.cache_key}@{base_url}"


class OrgConfigService:
    def __init__(
        self,
        *,
        fetch: FetchManagedConfig = fetch_managed_config,
        store: CacheStore[OrgConfigKey, str] | None = None,
        policy: RefreshPolicy = ORG_CONFIG_POLICY,
        first_wait_s: float = MANAGED_CONFIG_FETCH_BUDGET,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetch_managed_config = fetch
        self._first_wait_s = first_wait_s
        self._cache: RemoteCache[OrgConfigKey, str] = RemoteCache(
            "Org config",
            policy,
            self._fetch,
            store=store if store is not None else DiskOrgConfigStore(),
            clock=clock,
        )

    @staticmethod
    def key_for(
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
    ) -> OrgConfigKey | None:
        """The key this config's org config is held under.

        Read it before any org config applies: the org config can change the
        provider and endpoint it is made of.
        """
        return OrgConfigService.key_for_config(orchestrator.config)

    @staticmethod
    def key_for_config(config: VibeConfigSchema) -> OrgConfigKey | None:
        cache_key = managed_config_cache_key(config)
        if cache_key is None:
            return None
        provider = config.get_mistral_provider()
        api_key = resolve_api_key(provider.api_key_env_var) if provider else None
        if not api_key:
            return None
        return OrgConfigKey(cache_key, base_url=config.vibe_base_url, api_key=api_key)

    async def ensure(self, key: OrgConfigKey | None) -> AdminConfigApplyResult | None:
        """Make sure a copy is held before a session is built from it.

        Never waits when one is held. With none, waits for the first answer
        within ``first_wait_s``, unless a fetch failed recently and is not
        retried yet. Returns why there is still none.
        """
        if key is None:
            return None
        try:
            entry = await self._cache.first(key, timeout_s=self._first_wait_s)
        except RemoteFetchFailedError as exc:
            return _fetch_failure(exc)
        if entry is None:
            return AdminConfigApplyResult(
                AdminConfigOutcome.FETCH_FAILED, error="timed out"
            )
        return None

    async def load(self, key: OrgConfigKey | None) -> str | None:
        """The copy held, reading the disk the first time; ``""`` when the org
        enforces nothing, ``None`` when there is no copy.
        """
        if key is None:
            return None
        entry = await self._cache.load(key)
        return entry.value if entry is not None else None

    def failed_recently(self, key: OrgConfigKey) -> bool:
        return self._cache.failure(key) is not None

    async def refresh(self, key: OrgConfigKey | None) -> str | AdminConfigApplyResult:
        """Fetch now (``/reload``), whatever the copy's age, for every session.

        The TOML (``""`` when the org enforces nothing), or why there is none.
        """
        if key is None:
            return AdminConfigApplyResult(AdminConfigOutcome.NO_API_KEY)
        try:
            entry = await self._cache.refresh(key)
        except RemoteFetchFailedError as exc:
            return _fetch_failure(exc)
        return entry.value

    def followed(self, key: OrgConfigKey) -> Followed[OrgConfigKey, str]:
        """The copy a session follows, for a ``RemoteFollower``."""
        return self._cache.followed(key)

    async def close(self) -> None:
        await self._cache.close()

    async def _fetch(self, key: OrgConfigKey) -> str:
        fetched = await self._fetch_managed_config(key.base_url, key.api_key)
        if fetched.error is not None:
            raise OrgConfigFetchError(AdminConfigOutcome.FETCH_FAILED, fetched.error)
        managed = fetched.config
        if managed is None or not managed.is_enabled or managed.toml is None:
            return ""
        try:
            tomllib.loads(managed.toml)
        except tomllib.TOMLDecodeError as exc:
            # Not held: the copy already held stays in force.
            raise OrgConfigFetchError(
                AdminConfigOutcome.PARSE_FAILED, str(exc)
            ) from exc
        return managed.toml


def _fetch_failure(exc: RemoteFetchFailedError) -> AdminConfigApplyResult:
    cause = exc.cause
    if isinstance(cause, OrgConfigFetchError):
        return AdminConfigApplyResult(cause.outcome, error=str(cause))
    if isinstance(cause, TimeoutError):
        return AdminConfigApplyResult(
            AdminConfigOutcome.FETCH_FAILED, error="timed out"
        )
    return AdminConfigApplyResult(AdminConfigOutcome.FETCH_FAILED, error=str(cause))


__all__ = [
    "ORG_CONFIG_POLICY",
    "ORG_CONFIG_REFRESH_AFTER_S",
    "DiskOrgConfigStore",
    "OrgConfigKey",
    "OrgConfigService",
]
