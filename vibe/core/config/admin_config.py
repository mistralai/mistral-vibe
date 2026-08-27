from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto

import httpx
from pydantic import BaseModel, ConfigDict

from vibe.core.utils.retry import async_retry
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

# Served by Vibe server app, so this is appended to vibe_base_url
# e.g. https://chat.mistral.ai/api/v1/code/managed-config.
MANAGED_CONFIG_PATH = "/api/v1/code/managed-config"

# Per-request timeout for the managed-config HTTP call.
MANAGED_CONFIG_TIMEOUT = 2.0

# Number of attempts before giving up (1 initial + retries).
MANAGED_CONFIG_RETRY_TRIES = 3


class AdminConfigOutcome(StrEnum):
    APPLIED = auto()
    DISABLED = auto()
    NO_API_KEY = auto()
    FETCH_FAILED = auto()
    PARSE_FAILED = auto()
    APPLY_FAILED = auto()


class ManagedConfig(BaseModel):
    """Admin-managed config served by Vibe for the caller's org/workspace."""

    model_config = ConfigDict(extra="ignore")

    state: str
    toml: str | None = None

    @property
    def is_enabled(self) -> bool:
        return self.state == "enabled" and bool(self.toml)


@dataclass(slots=True)
class ManagedConfigResult:
    """Outcome of the fetch step. ``error`` is set only on a fetch failure."""

    config: ManagedConfig | None = None
    error: str | None = None


@dataclass(slots=True)
class AdminConfigApplyResult:
    """Outcome of loading org-enforced config into the admin layer."""

    outcome: AdminConfigOutcome
    enforced_keys: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def applied(self) -> bool:
        return self.outcome is AdminConfigOutcome.APPLIED


@async_retry(tries=MANAGED_CONFIG_RETRY_TRIES, delay_seconds=0.5, backoff_factor=2.0)
async def _fetch_response(url: str, headers: dict[str, str]) -> httpx.Response:
    async with VibeAsyncHTTPClient(verify=build_ssl_context()) as client:
        response = await client.get(
            url, headers=headers, timeout=MANAGED_CONFIG_TIMEOUT
        )
        response.raise_for_status()
        return response


async def fetch_managed_config(base_url: str, api_key: str) -> ManagedConfigResult:
    """Fetch the org/workspace-enforced config for the given Mistral API key.

    ``base_url`` is the Vibe server base (``vibe_base_url``, e.g.
    ``https://chat.mistral.ai``) that serves the managed-config endpoint.

    Transient failures (network errors, 5xx, 429) are retried with exponential
    backoff. On any failure the returned result carries an ``error`` message and
    no config, so the admin layer stays empty and a fetch problem has no impact
    on the client.
    """
    url = f"{base_url.rstrip('/')}{MANAGED_CONFIG_PATH}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = await _fetch_response(url, headers)
    except httpx.HTTPStatusError as exc:
        logger.debug(
            "Managed config fetch status=%s url=%s", exc.response.status_code, url
        )
        return ManagedConfigResult(error=f"HTTP {exc.response.status_code}")
    except httpx.RequestError as exc:
        logger.debug("Managed config fetch failed url=%s error=%s", url, exc)
        return ManagedConfigResult(error=str(exc))

    try:
        config = ManagedConfig.model_validate(response.json())
    except (ValueError, TypeError) as exc:
        logger.debug("Managed config parse failed url=%s error=%s", url, exc)
        return ManagedConfigResult(error=str(exc))
    return ManagedConfigResult(config=config)
