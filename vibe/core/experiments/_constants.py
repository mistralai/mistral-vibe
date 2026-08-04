from __future__ import annotations

from typing import Final

GROWTHBOOK_EVAL_PATH_TEMPLATE: Final = "/api/eval/{client_key}"

EVAL_REQUEST_TIMEOUT_SECONDS: Final = 5.0


def build_eval_url(api_host: str, client_key: str) -> str | None:
    api_host = api_host.strip().rstrip("/")
    client_key = client_key.strip()
    if not api_host or not client_key:
        return None
    return f"{api_host}{GROWTHBOOK_EVAL_PATH_TEMPLATE.format(client_key=client_key)}"
