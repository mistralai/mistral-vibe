from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vibe.core.types import Backend

if TYPE_CHECKING:
    from collections.abc import Callable

    from vibe.core.config import ProviderConfig
    from vibe.core.llm.types import BackendLike


def _create_mistral_backend(**kwargs: Any) -> BackendLike:
    from vibe.core.llm.backend.mistral import MistralBackend

    return MistralBackend(**kwargs)


def _create_generic_backend(**kwargs: Any) -> BackendLike:
    from vibe.core.llm.backend.generic import GenericBackend

    return GenericBackend(**kwargs)


# The factories import the backend modules on first use rather than at module
# level: the backends pull in heavy dependencies that would otherwise slow CLI
# startup.
BACKEND_FACTORY: dict[Backend, Callable[..., BackendLike]] = {
    Backend.MISTRAL: _create_mistral_backend,
    Backend.GENERIC: _create_generic_backend,
}


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
