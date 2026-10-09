"""Global, cross-session cache of the last successful GrowthBook eval response.

The GrowthBook remote eval (identity ``/users/me`` + eval POST) is on the
startup critical path.
Caching the last successful response per user lets a fresh session apply the
last-known variants *optimistically* from local disk — no network — while the
real eval runs in the background and hot-swaps the config once it resolves.

The cache is keyed by the hashed Mistral API key (GrowthBook's bucketing
attribute), TTL-bounded so very stale variants are never applied, and written
atomically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibe.core.experiments._constants import EVAL_CACHE_TTL_SECONDS
from vibe.core.experiments.models import EvalResponse
from vibe.core.paths import EXPERIMENT_EVAL_CACHE_FILE
from vibe.core.utils.keyed_json_cache import load_keyed_entry, store_keyed_entry
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


def load_cached_eval_response(config: VibeConfigSchema) -> EvalResponse | None:
    key = _cache_key(config)
    if key is None:
        return None
    return load_keyed_entry(
        EXPERIMENT_EVAL_CACHE_FILE.path,
        key,
        ttl_seconds=EVAL_CACHE_TTL_SECONDS,
        parse=_parse_eval_payload,
    )


def store_cached_eval_response(
    config: VibeConfigSchema, response: EvalResponse
) -> None:
    key = _cache_key(config)
    if key is None:
        return
    store_keyed_entry(
        EXPERIMENT_EVAL_CACHE_FILE.path, key, response.model_dump(mode="json")
    )


def clear_cached_eval_responses() -> None:
    try:
        EXPERIMENT_EVAL_CACHE_FILE.path.unlink(missing_ok=True)
    except OSError:
        logger.debug("Failed to delete experiment eval cache file", exc_info=True)


def _parse_eval_payload(payload: Any) -> EvalResponse | None:
    if not isinstance(payload, dict):
        return None
    try:
        return EvalResponse.model_validate(payload)
    except Exception:
        return None


def _cache_key(config: VibeConfigSchema) -> str | None:
    # The eval cache is telemetry-gated: no telemetry, no bucketing.
    if not config.enable_telemetry or not config.experiments.enable:
        return None
    from vibe.core.telemetry.send import mistral_credential_cache_key

    return mistral_credential_cache_key(config)
