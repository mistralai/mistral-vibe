from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.mistral import MistralBackend
from vibe.core.types import Backend

if TYPE_CHECKING:
    from vibe.core.config import ProviderConfig
    from vibe.core.llm.types import BackendLike


BACKEND_FACTORY = {Backend.MISTRAL: MistralBackend, Backend.GENERIC: GenericBackend}


def create_backend(
    *,
    provider: ProviderConfig,
    timeout: float = 720.0,
    retry_max_elapsed_time: float = 300.0,
    enable_otel: bool = False,
) -> BackendLike:
    factory = BACKEND_FACTORY[provider.backend]
    if provider.backend == Backend.MISTRAL:
        return factory(
            provider=provider,
            timeout=timeout,
            retry_max_elapsed_time=retry_max_elapsed_time,
            enable_otel=enable_otel,
        )
    return factory(provider=provider, timeout=timeout)
