from __future__ import annotations

from typing import Final

MODEL_CATALOG_PATH: Final = "/model-catalog"

MODEL_CATALOG_SURFACE: Final = "vibe-code"

# Matches EVAL_REQUEST_TIMEOUT_SECONDS so a first-ever fetch holds the first
# turn no longer than an experiment eval.
MODEL_CATALOG_TIMEOUT_SECONDS: Final = 5.0

# A catalog this old may advertise retired models. Longer than the
# experiment-eval TTL because a model list moves far less than a variant.
MODEL_CATALOG_CACHE_TTL_SECONDS: Final = 30 * 24 * 60 * 60


def build_model_catalog_url(api_base: str) -> str | None:
    """Build the catalog URL from a Mistral provider's ``api_base``.

    Returns None when ``api_base`` is empty, so a misconfigured provider makes
    the fetch a no-op rather than a request to a garbage URL.
    """
    api_base = api_base.strip().rstrip("/")
    if not api_base:
        return None
    return f"{api_base}{MODEL_CATALOG_PATH}?surface={MODEL_CATALOG_SURFACE}"
