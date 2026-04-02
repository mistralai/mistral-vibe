from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.config import (
    DEFAULT_TRANSCRIBE_MODELS,
    DEFAULT_TRANSCRIBE_PROVIDERS,
    TranscribeClient,
    TranscribeModelConfig,
    TranscribeProviderConfig,
)


class TestTranscribeConfigDefaults:
    def test_default_transcribe_providers_loaded(self) -> None:
        config = build_test_vibe_config()
        assert len(config.transcribe_providers) == len(DEFAULT_TRANSCRIBE_PROVIDERS)
        assert config.transcribe_providers[0].name == "mistral"
        assert config.transcribe_providers[0].api_base == "wss://api.mistral.ai"

    def test_default_transcribe_models_loaded(self) -> None:
        config = build_test_vibe_config()
        assert len(config.transcribe_models) == len(DEFAULT_TRANSCRIBE_MODELS)
        assert config.transcribe_models[0].alias == "voxtral-realtime"
        assert (
            config.transcribe_models[0].name == "voxtral-mini-transcribe-realtime-2602"
        )

    def test_default_active_transcribe_model(self) -> None:
        config = build_test_vibe_config()
        assert config.active_transcribe_model == "voxtral-realtime"


class TestGetActiveTranscribeModel:
    def test_resolves_by_alias(self) -> None:
        config = build_test_vibe_config()
        model = config.get_active_transcribe_model()
        assert model.alias == "voxtral-realtime"
        assert model.name == "voxtral-mini-transcribe-realtime-2602"

    def test_raises_for_unknown_alias(self) -> None:
        config = build_test_vibe_config(active_transcribe_model="nonexistent")
        with pytest.raises(ValueError, match="not found in configuration"):
            config.get_active_transcribe_model()


class TestGetTranscribeProviderForModel:
    def test_resolves_by_name(self) -> None:
        config = build_test_vibe_config()
        model = config.get_active_transcribe_model()
        provider = config.get_transcribe_provider_for_model(model)
        assert provider.name == "mistral"
        assert provider.api_base == "wss://api.mistral.ai"

    def test_raises_for_unknown_provider(self) -> None:
        config = build_test_vibe_config(
            transcribe_models=[
                TranscribeModelConfig(
                    name="test-model", provider="nonexistent", alias="test"
                )
            ],
            active_transcribe_model="test",
        )
        model = config.get_active_transcribe_model()
        with pytest.raises(ValueError, match="not found in configuration"):
            config.get_transcribe_provider_for_model(model)


class TestTranscribeModelUniqueness:
    def test_duplicate_aliases_raise(self) -> None:
        with pytest.raises(ValueError, match="Duplicate transcribe model alias"):
            build_test_vibe_config(
                transcribe_models=[
                    TranscribeModelConfig(
                        name="model-a", provider="mistral", alias="same-alias"
                    ),
                    TranscribeModelConfig(
                        name="model-b", provider="mistral", alias="same-alias"
                    ),
                ],
                active_transcribe_model="same-alias",
            )


class TestTranscribeModelConfig:
    def test_alias_defaults_to_name(self) -> None:
        model = TranscribeModelConfig.model_validate({
            "name": "my-model",
            "provider": "mistral",
        })
        assert model.alias == "my-model"

    def test_explicit_alias(self) -> None:
        model = TranscribeModelConfig(
            name="my-model", provider="mistral", alias="custom-alias"
        )
        assert model.alias == "custom-alias"

    def test_default_values(self) -> None:
        model = TranscribeModelConfig(
            name="my-model", provider="mistral", alias="my-model"
        )
        assert model.sample_rate == 16000
        assert model.encoding == "pcm_s16le"
        assert model.language == "en"
        assert model.target_streaming_delay_ms == 500


class TestTranscribeProviderConfig:
    def test_default_values(self) -> None:
        provider = TranscribeProviderConfig(name="test")
        assert provider.api_base == "wss://api.mistral.ai"
        assert provider.api_key_env_var == ""
        assert provider.client == TranscribeClient.MISTRAL
