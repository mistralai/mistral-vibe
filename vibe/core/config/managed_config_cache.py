"""Disk cache of the last fetched admin-managed config TOML.

Every session build applies it, and the process refreshes it in the
background once it is older than the org config's refresh limit (see
``vibe.app_server._org_config_service``). An answer that the org enforces nothing is
cached as an empty policy.

The cache is keyed by the hashed Mistral API key (the shared per-user cache
key), TTL-bounded so very stale policy is never applied, and written
atomically by the shared keyed-JSON cache. A stale, corrupt, or missing
entry is never fatal: the caller gets ``None`` and the session starts the way
it would without a cache.

The key is computed once, from the config before any org config applies:
the org config can change the Mistral provider the key is derived from, and
a store keyed under the policy it stores would not be found by the next read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.paths import MANAGED_CONFIG_CACHE_FILE
from vibe.core.utils.keyed_json_cache import load_keyed_record, store_keyed_entry

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

MANAGED_CONFIG_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


def managed_config_cache_key(config: VibeConfigSchema) -> str | None:
    """The cache key for ``config``'s Mistral credential; ``None`` without one.

    Pass the config before the org config is applied (see the module docstring).
    """
    from vibe.core.telemetry.send import mistral_credential_cache_key

    return mistral_credential_cache_key(config)


def load_cached_managed_config(key: str | None) -> str | None:
    """Return the fresh cached managed-config TOML stored under ``key``."""
    record = load_cached_managed_config_record(key)
    return record[0] if record is not None else None


def load_cached_managed_config_record(key: str | None) -> tuple[str, int] | None:
    """The fresh cached TOML under ``key`` and when it was stored."""
    if key is None:
        return None
    return load_keyed_record(
        MANAGED_CONFIG_CACHE_FILE.path,
        key,
        ttl_seconds=MANAGED_CONFIG_CACHE_TTL_SECONDS,
        parse=lambda payload: payload if isinstance(payload, str) else None,
    )


def store_cached_managed_config(
    key: str | None, toml_text: str, *, stored_at: int | None = None
) -> None:
    """Cache the last fetched managed-config TOML under ``key``."""
    if key is None:
        return
    store_keyed_entry(
        MANAGED_CONFIG_CACHE_FILE.path, key, toml_text, stored_at=stored_at
    )
