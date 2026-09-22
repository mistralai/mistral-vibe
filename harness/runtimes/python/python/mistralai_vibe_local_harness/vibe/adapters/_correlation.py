"""Shared provider correlation-id capture for completion adapters.

The Mistral API returns a ``mistral-correlation-id`` response header that lets
the Host join analytics/feedback to the exact provider request. Both the native
Mistral adapter and the generic (OpenAI-compatible) adapter capture it the same
way — via an httpx response event hook — so Mistral-hosted models reachable
through either path report the id. Non-Mistral providers simply omit the header,
and the sink is called with ``None`` (a no-op for the Host-side holder).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import httpx

from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig

CORRELATION_ID_HEADER = "mistral-correlation-id"


def correlation_hook(
    config: LocalRuntimeAdapterConfig,
) -> Callable[[httpx.Response], Coroutine[Any, Any, None]]:
    """Build an httpx response hook that reports the provider correlation id.

    Fires on every response through the client — streaming and non-streaming
    alike — so ``correlation_id_sink`` sees the id regardless of the call shape.
    """

    async def _capture(response: httpx.Response) -> None:
        if config.correlation_id_sink is not None:
            config.correlation_id_sink(response.headers.get(CORRELATION_ID_HEADER))

    return _capture
