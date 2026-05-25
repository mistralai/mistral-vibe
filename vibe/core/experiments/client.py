from __future__ import annotations

from http import HTTPStatus

import httpx
from pydantic import ValidationError

from vibe.core.experiments._constants import (
    EVAL_REQUEST_TIMEOUT_SECONDS,
    build_eval_url,
)
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.logger import logger
from vibe.core.utils.http import build_ssl_context


class RemoteEvalClient:
    """Thin client for the GrowthBook proxy remote evaluation endpoint.

    Fail-open: any error (network, HTTP, JSON, validation) returns None and the
    caller falls back to default variants. Errors are logged at WARNING. When
    the constructed URL is empty (missing api_host or client_key), `evaluate`
    is a no-op that returns None without making a network call.
    """

    def __init__(self, *, url: str | None = None) -> None:
        self._url = url
        self._http: httpx.AsyncClient | None = None

    @classmethod
    def from_settings(cls, api_host: str, client_key: str) -> RemoteEvalClient:
        return cls(url=build_eval_url(api_host, client_key))

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(EVAL_REQUEST_TIMEOUT_SECONDS),
                verify=build_ssl_context(),
            )
        return self._http

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        if self._url is None:
            return None
        payload = {
            "attributes": attributes.model_dump(exclude_none=True),
            "forcedVariations": {},
            "forcedFeatures": [],
            "url": "",
        }
        try:
            response = await self._client.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("GrowthBook eval request failed: %s", exc)
            return None

        if response.status_code >= HTTPStatus.BAD_REQUEST:
            logger.warning(
                "GrowthBook eval returned status=%s body=%s",
                response.status_code,
                response.text[:200],
            )
            return None

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("GrowthBook eval returned non-JSON body: %s", exc)
            return None

        try:
            return EvalResponse.model_validate(data)
        except ValidationError as exc:
            logger.warning("GrowthBook eval payload failed validation: %s", exc)
            return None

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
