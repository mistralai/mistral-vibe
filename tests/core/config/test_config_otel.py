from __future__ import annotations

import pytest

from tests.constants import ANTHROPIC_BASE_URL
from vibe.core.config import OtelSpanExporterConfig, ProviderConfig, VibeConfigSchema
from vibe.core.tracing import build_otel_span_exporter_config
from vibe.core.types import Backend


def _exporter_config(config: VibeConfigSchema) -> OtelSpanExporterConfig | None:
    return build_otel_span_exporter_config(
        config.otel_endpoint, config.get_mistral_provider()
    )


class TestOtelSpanExporterConfig:
    def test_derives_endpoint_from_mistral_provider(
        self, vibe_config: VibeConfigSchema, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-test")
        config = vibe_config.model_copy(
            update={
                "providers": [
                    ProviderConfig(
                        name="mistral",
                        api_base="https://customer.mistral.ai/v1",
                        backend=Backend.MISTRAL,
                    )
                ]
            }
        )
        result = _exporter_config(config)
        assert result is not None
        assert result.endpoint == "https://customer.mistral.ai/telemetry/v1/traces"
        assert result.headers == {"Authorization": "Bearer sk-test"}

    @pytest.mark.parametrize(
        ("api_base", "api_key_env_var", "api_key"),
        [
            ("https://api.eu.mistral.ai/v1", "EU_KEY", "sk-eu"),
            ("https://api.us.mistral.ai/v1", "US_KEY", "sk-us"),
        ],
    )
    def test_uses_global_endpoint_for_public_regional_api_host(
        self,
        api_base: str,
        api_key_env_var: str,
        api_key: str,
        vibe_config: VibeConfigSchema,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(api_key_env_var, api_key)
        config = vibe_config.model_copy(
            update={
                "providers": [
                    ProviderConfig(
                        name="mistral-regional",
                        api_base=api_base,
                        api_key_env_var=api_key_env_var,
                        backend=Backend.MISTRAL,
                    )
                ]
            }
        )
        result = _exporter_config(config)
        assert result is not None
        assert result.endpoint == "https://api.mistral.ai/telemetry/v1/traces"
        assert result.headers == {"Authorization": f"Bearer {api_key}"}

    def test_falls_back_to_default_when_no_mistral_provider(
        self, vibe_config: VibeConfigSchema, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-fallback")
        config = vibe_config.model_copy(
            update={
                "providers": [
                    ProviderConfig(
                        name="anthropic", api_base=f"{ANTHROPIC_BASE_URL}/v1"
                    )
                ]
            }
        )
        result = _exporter_config(config)
        assert result is not None
        assert result.endpoint == "https://api.mistral.ai/telemetry/v1/traces"
        assert result.headers == {"Authorization": "Bearer sk-fallback"}

    def test_default_providers(
        self, vibe_config: VibeConfigSchema, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-default")
        result = _exporter_config(vibe_config)
        assert result is not None
        assert result.endpoint == "https://api.mistral.ai/telemetry/v1/traces"

    def test_resolves_api_key_from_keyring(
        self, vibe_config: VibeConfigSchema, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Key stored only in the OS keyring (no env var) must still authenticate OTEL.
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setattr(
            "keyring.get_password", lambda service, username: "sk-keyring"
        )
        result = _exporter_config(vibe_config)
        assert result is not None
        assert result.headers == {"Authorization": "Bearer sk-keyring"}

    def test_returns_none_and_warns_when_api_key_missing(
        self,
        vibe_config: VibeConfigSchema,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        with caplog.at_level("WARNING"):
            assert _exporter_config(vibe_config) is None
        assert "OTEL tracing enabled but MISTRAL_API_KEY is not set" in caplog.text

    def test_custom_api_key_env_var(
        self, vibe_config: VibeConfigSchema, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        monkeypatch.setenv("MY_CUSTOM_KEY", "sk-custom")
        config = vibe_config.model_copy(
            update={
                "providers": [
                    ProviderConfig(
                        name="mistral-onprem",
                        api_base="https://onprem.corp.com/v1",
                        api_key_env_var="MY_CUSTOM_KEY",
                        backend=Backend.MISTRAL,
                    )
                ]
            }
        )
        result = _exporter_config(config)
        assert result is not None
        assert result.endpoint == "https://onprem.corp.com/telemetry/v1/traces"
        assert result.headers == {"Authorization": "Bearer sk-custom"}

    def test_explicit_otel_endpoint_appends_default_traces_path(
        self, vibe_config: VibeConfigSchema
    ) -> None:
        config = vibe_config.model_copy(
            update={"otel_endpoint": "https://my-collector:4318"}
        )
        result = _exporter_config(config)
        assert result is not None
        assert result == OtelSpanExporterConfig(
            endpoint="https://my-collector:4318/v1/traces"
        )
        assert result.headers is None

    def test_explicit_otel_endpoint_preserves_path_prefix(
        self, vibe_config: VibeConfigSchema
    ) -> None:
        config = vibe_config.model_copy(
            update={"otel_endpoint": "https://my-collector:4318/api/public/otel"}
        )
        result = _exporter_config(config)
        assert result is not None
        assert result == OtelSpanExporterConfig(
            endpoint="https://my-collector:4318/api/public/otel/v1/traces"
        )
        assert result.headers is None
