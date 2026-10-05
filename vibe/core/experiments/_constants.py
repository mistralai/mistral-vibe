from __future__ import annotations

from typing import Final

GROWTHBOOK_EVAL_PATH_TEMPLATE: Final = "/api/eval/{client_key}"

EVAL_REQUEST_TIMEOUT_SECONDS: Final = 5.0

# Stdlib-only module: the `vibe` launcher reads the eval cache before any heavy import.
EVAL_CACHE_FILE_NAME: Final = "experiment_eval_cache.json"
EVAL_CACHE_TTL_SECONDS: Final = 7 * 24 * 60 * 60


def build_eval_url(api_host: str, client_key: str) -> str | None:
    api_host = api_host.strip().rstrip("/")
    client_key = client_key.strip()
    if not api_host or not client_key:
        return None
    return f"{api_host}{GROWTHBOOK_EVAL_PATH_TEMPLATE.format(client_key=client_key)}"
