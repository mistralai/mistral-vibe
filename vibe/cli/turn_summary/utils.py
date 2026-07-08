from __future__ import annotations

from vibe.core.config import AnyVibeConfig, ModelConfig, resolve_api_key
from vibe.core.llm.backend.factory import create_backend
from vibe.core.llm.types import BackendLike

NARRATOR_MODEL = ModelConfig(
    name="mistral-vibe-cli-fast",
    provider="mistral",
    alias="mistral-small",
    input_price=0.1,
    output_price=0.3,
)


def create_narrator_backend(
    config: AnyVibeConfig,
) -> tuple[BackendLike, ModelConfig] | None:
    try:
        provider = config.get_provider_for_model(NARRATOR_MODEL)
    except ValueError:
        return None
    if provider.api_key_env_var and not resolve_api_key(provider.api_key_env_var):
        return None
    backend = create_backend(
        provider=provider,
        timeout=config.api_timeout,
        retry_max_elapsed_time=config.api_retry_max_elapsed_time,
    )
    return backend, NARRATOR_MODEL
