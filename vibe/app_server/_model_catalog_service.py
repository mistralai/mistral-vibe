"""The model catalog, held and refreshed once per OS process.

Every session used to fetch its own catalog when it opened, sharing only the
copy on disk. Now the process holds one catalog per account and endpoint, with
the copy on disk for the next process: a session is served the one held at
once, an old one is refreshed in the background, a failure backs off for every
session, and a session only waits, within its cap, for an account never
fetched on this machine. Each open session follows the catalog held
(``followed``) and loads a change at its next turn boundary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import math
import time
from typing import Final

from vibe.core.config import VibeConfigSchema
from vibe.core.config._defaults import DEFAULT_MISTRAL_API_BASE
from vibe.core.model_catalog._constants import (
    MODEL_CATALOG_CACHE_TTL_SECONDS,
    MODEL_CATALOG_TIMEOUT_SECONDS,
)
from vibe.core.model_catalog.cache import (
    load_cached_model_catalog,
    store_cached_model_catalog,
)
from vibe.core.model_catalog.client import fetch_model_catalog
from vibe.core.model_catalog.models import ModelCatalogResponse
from vibe.core.platform_auth import (
    PlatformCredentialFactory,
    PlatformCredentialProvider,
)
from vibe.core.platform_auth._mistral import (
    fetched_for_current_account,
    mistral_credentials,
    resolve_principal,
)
from vibe.core.utils.fetch_wait import FetchFailures
from vibe.core.utils.remote_cache import (
    Cached,
    CacheStore,
    Followed,
    RefreshPolicy,
    RemoteCache,
    RemoteFetchFailedError,
)
from vibe.observability.logging import logger

# The HTTP timeout bounds each connection phase; this bounds the whole fetch,
# a token refresh included.
MODEL_CATALOG_FETCH_TIMEOUT_S: Final = 3 * MODEL_CATALOG_TIMEOUT_SECONDS

MODEL_CATALOG_POLICY: Final = RefreshPolicy(
    refresh_after_s=10 * 60,
    max_age_s=MODEL_CATALOG_CACHE_TTL_SECONDS,
    retry_after_s=30,
    max_retry_after_s=10 * 60,
    fetch_timeout_s=MODEL_CATALOG_FETCH_TIMEOUT_S,
)

FetchModelCatalog = Callable[
    [str, PlatformCredentialProvider], Awaitable[ModelCatalogResponse | None]
]


@dataclass(frozen=True, slots=True)
class ModelCatalogKey:
    """One account's catalog, on the endpoint it is fetched from. The
    credential is carried for the fetch, never compared.
    """

    credential: str
    api_base: str
    credentials: PlatformCredentialProvider = field(compare=False, repr=False)

    @property
    def disk_key(self) -> str:
        # The default endpoint keeps the bare key that existing caches hold.
        api_base = self.api_base.rstrip("/")
        if api_base == DEFAULT_MISTRAL_API_BASE:
            return self.credential
        return f"{self.credential}@{api_base}"

    def is_current(self) -> bool:
        return fetched_for_current_account(self.credentials, self.credential)

    @classmethod
    def current(
        cls, config: VibeConfigSchema, credentials: PlatformCredentialFactory | None
    ) -> ModelCatalogKey | None:
        """The key *config* reads its catalog under, without resolving the
        credential; ``None`` with the catalog off or no known account.
        """
        if not config.experimental_enable_model_catalog:
            return None
        mistral = mistral_credentials(config, credentials)
        if mistral is None or (principal := mistral.cached_principal()) is None:
            return None
        return cls(principal, mistral.provider.api_base, mistral.credentials)

    @classmethod
    async def of(
        cls, config: VibeConfigSchema, credentials: PlatformCredentialFactory | None
    ) -> ModelCatalogKey | None:
        """``current``, resolving the credential's principal when not known
        yet (a token refresh, a tenant discovery).
        """
        if not config.experimental_enable_model_catalog:
            return None
        mistral = mistral_credentials(config, credentials)
        if mistral is None or (principal := await resolve_principal(mistral)) is None:
            return None
        return cls(principal, mistral.provider.api_base, mistral.credentials)


class ModelCatalogFetchFailedError(Exception):
    pass


class ModelCatalogAccountChangedError(ModelCatalogFetchFailedError):
    pass


class DiskModelCatalogStore:
    def load(self, key: ModelCatalogKey) -> Cached[ModelCatalogResponse] | None:
        record = load_cached_model_catalog(key.disk_key)
        if record is None:
            return None
        catalog, stored_at = record
        return Cached(catalog, stored_at=stored_at)

    def save(self, key: ModelCatalogKey, entry: Cached[ModelCatalogResponse]) -> None:
        if not key.is_current():
            return
        store_cached_model_catalog(
            key.disk_key, entry.value, stored_at=int(entry.stored_at)
        )


class ModelCatalogService:
    def __init__(
        self,
        *,
        fetch: FetchModelCatalog = fetch_model_catalog,
        policy: RefreshPolicy = MODEL_CATALOG_POLICY,
        store: CacheStore[ModelCatalogKey, ModelCatalogResponse] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._fetch_catalog = fetch
        # Set once a first fetch failed or outlasted its cap: no later session
        # of the process holds its first turn for one.
        self.failures = FetchFailures()
        self._cache: RemoteCache[ModelCatalogKey, ModelCatalogResponse] = RemoteCache(
            "Model catalog",
            policy,
            self._fetch,
            store=store if store is not None else DiskModelCatalogStore(),
            clock=clock,
        )

    async def load(self, key: ModelCatalogKey | None) -> ModelCatalogResponse | None:
        if key is None:
            return None
        entry = await self._cache.load(key)
        return entry.value if entry is not None else None

    async def ensure(self, key: ModelCatalogKey) -> ModelCatalogResponse | None:
        """The catalog held for *key*, or the first one fetched for it;
        ``None`` when the fetch failed, or failed recently.
        """
        try:
            # The fetch bounds itself, after reading the disk copy: a shorter
            # wait here would give up on a fetch about to answer.
            entry = await self._cache.first(key, timeout_s=math.inf)
        except RemoteFetchFailedError as exc:
            # Only a failure that leaves nothing held: a failed refresh behind
            # one is no reason for a later session to skip its wait. Nor is a
            # sign-in during the fetch: the new account's catalog is still due.
            if not isinstance(exc.cause, ModelCatalogAccountChangedError):
                self.failures.mark()
            return None
        if entry is None:
            self.failures.mark()
            return None
        return entry.value

    async def refresh(self, key: ModelCatalogKey) -> ModelCatalogResponse | None:
        try:
            entry = await self._cache.refresh(key)
        except RemoteFetchFailedError:
            return None
        return entry.value

    def followed(
        self, key: ModelCatalogKey
    ) -> Followed[ModelCatalogKey, ModelCatalogResponse]:
        return self._cache.followed(key)

    async def close(self) -> None:
        await self._cache.close()

    async def _fetch(self, key: ModelCatalogKey) -> ModelCatalogResponse:
        catalog = await self._fetch_catalog(key.api_base, key.credentials)
        if catalog is None:
            raise ModelCatalogFetchFailedError("Model catalog fetch failed")
        if not key.is_current():
            # Neither held for this key nor written to disk.
            raise ModelCatalogAccountChangedError("Signed in to another account")
        logger.info(
            "Model catalog fetched: %d models, recommended=%s",
            len(catalog.models),
            next((entry.id for entry in catalog.models if entry.recommended), "none"),
        )
        return catalog


__all__ = ["MODEL_CATALOG_POLICY", "ModelCatalogKey", "ModelCatalogService"]
