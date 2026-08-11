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

import json
import os
import time
from typing import TYPE_CHECKING, Any, Final

from vibe.core.experiments.models import EvalResponse
from vibe.core.paths import EXPERIMENT_EVAL_CACHE_FILE
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

_EVAL_CACHE_TTL_SECONDS: Final = 7 * 24 * 60 * 60


def load_cached_eval_response(config: VibeConfigSchema) -> EvalResponse | None:
    key = _cache_key(config)
    if key is None:
        return None
    entry = _read_entries().get(key)
    if not isinstance(entry, dict):
        return None
    stored_at = entry.get("stored_at_timestamp")
    payload = entry.get("payload")
    if not isinstance(stored_at, int) or not isinstance(payload, dict):
        return None
    if stored_at <= int(time.time()) - _EVAL_CACHE_TTL_SECONDS:
        return None
    try:
        return EvalResponse.model_validate(payload)
    except Exception:
        return None


def store_cached_eval_response(
    config: VibeConfigSchema, response: EvalResponse
) -> None:
    key = _cache_key(config)
    if key is None:
        return
    entries = _read_entries()
    entries[key] = {
        "stored_at_timestamp": int(time.time()),
        "payload": response.model_dump(mode="json"),
    }
    _write_entries(entries)


def _cache_key(config: VibeConfigSchema) -> str | None:
    if not config.enable_telemetry or not config.experiments.enable:
        return None
    from vibe.core.experiments.manager import hash_api_key
    from vibe.core.telemetry.send import get_mistral_provider_and_api_key

    provider_and_key = get_mistral_provider_and_api_key(config)
    if provider_and_key is None:
        return None
    _provider, api_key = provider_and_key
    return hash_api_key(api_key)


def _read_entries() -> dict[str, Any]:
    try:
        with EXPERIMENT_EVAL_CACHE_FILE.path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_entries(entries: dict[str, Any]) -> None:
    cache_path = EXPERIMENT_EVAL_CACHE_FILE.path
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
        logger.debug(
            "Failed to write experiment eval cache file %s", cache_path, exc_info=True
        )
