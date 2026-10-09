from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx

from vibe.core.model_catalog._constants import (
    MODEL_CATALOG_TIMEOUT_SECONDS,
    build_model_catalog_url,
)
from vibe.core.model_catalog.models import (
    ModelCatalogResponse,
    parse_model_catalog_response,
)
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

if TYPE_CHECKING:
    from vibe.core.config import ProviderConfig


class ModelCatalogClient:
    """Thin hand-rolled client for the ``/model-catalog`` endpoint.

    The endpoint is excluded from the generated SDKs, so the client and its
    response types live here. Fail-open like the GrowthBook eval client: any
    error (network, HTTP, JSON, validation) returns None and the caller falls
    back to the cache or local config. When the constructed URL is empty, the
    fetch is a no-op that returns None without making a network call.
    """

    def __init__(self, *, url: str | None = None) -> None:
        self._url = url
        self._http: VibeAsyncHTTPClient | None = None

    @classmethod
    def from_provider(cls, provider: ProviderConfig) -> ModelCatalogClient:
        return cls(url=build_model_catalog_url(provider.api_base))

    @property
    def _client(self) -> VibeAsyncHTTPClient:
        if self._http is None:
            self._http = VibeAsyncHTTPClient(
                timeout=httpx.Timeout(MODEL_CATALOG_TIMEOUT_SECONDS),
                verify=build_ssl_context(),
            )
        return self._http

    async def fetch(self, api_key: str) -> ModelCatalogResponse | None:
        if self._url is None:
            return None
        try:
            response = await self._client.get(
                self._url, headers={"Authorization": f"Bearer {api_key}"}
            )
        except httpx.HTTPError as exc:
            logger.warning("Model catalog request failed: %s", exc)
            return None

        if response.status_code >= HTTPStatus.BAD_REQUEST:
            logger.warning(
                "Model catalog returned status=%s body=%s",
                response.status_code,
                response.text[:200],
            )
            return None

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("Model catalog returned non-JSON body: %s", exc)
            return None

        catalog = parse_model_catalog_response(data)
        if catalog is None:
            logger.warning("Model catalog payload is not a catalog: %.200s", data)
        return catalog

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
