from __future__ import annotations

import os

from vibe.core.config import ModelConfig, VibeConfig
from vibe.core.llm.backend.factory import BACKEND_FACTORY
from vibe.core.llm.types import BackendLike

NARRATOR_MODEL = ModelConfig(
    name="mistral-vibe-cli-fast",
    provider="mistral",
    alias="mistral-small",
    input_price=0.1,
    output_price=0.3,
)


def create_narrator_backend(
    config: VibeConfig,
) -> tuple[BackendLike, ModelConfig] | None:
    try:
        provider = config.get_provider_for_model(NARRATOR_MODEL)
    except ValueError:
        return None
    if provider.api_key_env_var and not os.getenv(provider.api_key_env_var):
        return None
    backend = BACKEND_FACTORY[provider.backend](
        provider=provider, timeout=config.api_timeout
    )
    return backend, NARRATOR_MODEL
