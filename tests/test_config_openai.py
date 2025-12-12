from __future__ import annotations

from chefchat.core.config import DEFAULT_MODELS, DEFAULT_PROVIDERS, Backend, VibeConfig


def test_openai_provider_in_defaults():
    """Verify OpenAI provider is in DEFAULT_PROVIDERS."""
    provider_names = [p.name for p in DEFAULT_PROVIDERS]
    assert "openai" in provider_names

    openai_provider = next(p for p in DEFAULT_PROVIDERS if p.name == "openai")
    assert openai_provider.api_base == "https://api.openai.com/v1"
    assert openai_provider.api_key_env_var == "OPENAI_API_KEY"
    assert openai_provider.api_style == "openai"
    assert openai_provider.backend == Backend.GENERIC

def test_openai_models_in_defaults():
    """Verify OpenAI models are in DEFAULT_MODELS with correct pricing."""
    model_names = [m.name for m in DEFAULT_MODELS]
    expected_models = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]

    for model in expected_models:
        assert model in model_names

    gpt4o = next(m for m in DEFAULT_MODELS if m.name == "gpt-4o")
    assert gpt4o.provider == "openai"
    assert gpt4o.alias == "gpt4o"
    assert gpt4o.input_price == 2.5
    assert gpt4o.output_price == 10.0

    gpt4o_mini = next(m for m in DEFAULT_MODELS if m.name == "gpt-4o-mini")
    assert gpt4o_mini.provider == "openai"
    assert gpt4o_mini.alias == "gpt4o-mini"
    assert gpt4o_mini.input_price == 0.15
    assert gpt4o_mini.output_price == 0.60

def test_openai_config_loading_no_key():
    """Verify config loads even if API key is missing (until we try to use it)."""
    # Simply loading defaults shouldn't crash
    # NOTE: VibeConfig.load() might check keys if env var is set but empty.
    # Here we simulate loading pure defaults.

    # We construct manually to avoid file I/O
    config = VibeConfig.model_construct(
        providers=DEFAULT_PROVIDERS,
        models=DEFAULT_MODELS
    )

    gpt4o = next(m for m in config.models if m.alias == "gpt4o")
    assert gpt4o.name == "gpt-4o"

    provider = config.get_provider_for_model(gpt4o)
    assert provider.name == "openai"
