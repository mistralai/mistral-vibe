"""Thin wrapper around the Mistral Python SDK.

Provides a singleton client and convenience helpers used by all
downstream services. Falls back to demo / mock mode when no API key
is configured so the app remains runnable for local development.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from backend.config import settings

logger = logging.getLogger(__name__)

_HAS_MISTRAL = False
try:
    from mistralai import Mistral

    _HAS_MISTRAL = True
except ImportError:
    logger.warning("mistralai SDK not installed — AI features will use mock responses")


@lru_cache(maxsize=1)
def get_client() -> Any:
    """Return a lazily-initialised Mistral client (or *None* in mock mode)."""
    if not _HAS_MISTRAL or not settings.mistral_api_key:
        logger.info("Running in mock mode (no MISTRAL_API_KEY)")
        return None
    return Mistral(api_key=settings.mistral_api_key)


async def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Run a chat completion and return the assistant text.

    When the Mistral SDK is not available or no API key is set the
    function returns a minimal JSON stub so callers can still exercise
    the full flow locally.
    """
    client = get_client()
    effective_model = model or settings.mistral_large_model

    if client is None:
        return _mock_response(messages, effective_model)

    kwargs: dict[str, Any] = {
        "model": effective_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    try:
        response = client.chat.complete(**kwargs)
        return response.choices[0].message.content  # type: ignore[union-attr]
    except Exception:
        logger.exception("Mistral chat completion failed")
        return _mock_response(messages, effective_model)


async def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using mistral-embed."""
    client = get_client()
    if client is None:
        return [[0.0] * 1024 for _ in texts]

    try:
        response = client.embeddings.create(
            model=settings.mistral_embed_model,
            inputs=texts,
        )
        return [item.embedding for item in response.data]
    except Exception:
        logger.exception("Mistral embedding failed")
        return [[0.0] * 1024 for _ in texts]


def _mock_response(messages: list[dict[str, str]], model: str) -> str:
    """Return a deterministic mock response for offline / demo use."""
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return json.dumps(
        {
            "mock": True,
            "model": model,
            "note": "No MISTRAL_API_KEY configured — returning mock data.",
            "echo": last_user[:200],
        }
    )
