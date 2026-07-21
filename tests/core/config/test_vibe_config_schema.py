from __future__ import annotations

from pathlib import Path

import keyring
import pytest

from vibe.core.config import (
    DEFAULT_THEME,
    MissingAPIKeyError,
    ModelConfig,
    ProviderConfig,
)
from vibe.core.config.vibe_schema import VibeConfigSchema


@pytest.mark.asyncio
async def test_full_toml_to_vibe_config_schema(tmp_path: Path) -> None:
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        """\
disable_welcome_banner_animation = true
api_timeout = 300.0
api_retry_max_elapsed_time = 120.0
active_model = "codestral"
disabled_tools = ["bash"]
default_agent = "plan"
enabled_skills = ["search"]
enable_otel = true

[[models]]
alias = "codestral"
name = "codestral-latest"
provider = "mistral"
"""
    )

    from vibe.core.config.layers.user import UserConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    layer = UserConfigLayer(path=toml_path)
    orchestrator = await ConfigOrchestrator[VibeConfigSchema].create(
        schema=VibeConfigSchema, layers=[layer], default_layer_resolver=lambda: layer
    )
    config = orchestrator.config

    assert config.disable_welcome_banner_animation is True
    assert config.api_timeout == 300.0
    assert config.api_retry_max_elapsed_time == 120.0
    assert config.active_model == "codestral"
    assert config.models["codestral"].alias == "codestral"
    assert "bash" in config.disabled_tools
    assert config.default_agent == "plan"
    assert "search" in config.enabled_skills
    assert config.enable_otel is True


def test_duplicate_model_alias_last_wins() -> None:
    config = VibeConfigSchema.model_validate({
        "models": [
            ModelConfig(name="model-a", provider="mistral", alias="same"),
            ModelConfig(name="model-b", provider="mistral", alias="same"),
        ]
    })

    assert list(config.models) == ["same"]
    assert config.models["same"].name == "model-b"


def test_unknown_active_model_falls_back_to_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        config = VibeConfigSchema(active_model="does-not-exist")

    fallback = next(iter(config.models))
    assert config.active_model == fallback
    assert config.get_active_model().alias == fallback
    assert (
        "Active model 'does-not-exist' is not in your configured models" in caplog.text
    )


def test_known_active_model_is_not_overridden(caplog: pytest.LogCaptureFixture) -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    with caplog.at_level("WARNING"):
        config = VibeConfigSchema.model_validate({
            "active_model": "b",
            "models": models,
        })
    assert config.active_model == "b"
    assert "is not in your configured models" not in caplog.text


def test_no_models_raises() -> None:
    with pytest.raises(ValueError, match="No models are configured"):
        VibeConfigSchema.model_validate({"models": []})


def test_compaction_model_provider_must_match_active() -> None:
    providers = [
        ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
        ),
        ProviderConfig(
            name="other",
            api_base="https://other.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
        ),
    ]
    compaction = ModelConfig(name="compact-model", provider="other", alias="compact")
    with pytest.raises(ValueError, match="must share the same provider"):
        VibeConfigSchema(compaction_model=compaction, providers=providers)


def test_check_api_key_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    with pytest.raises(MissingAPIKeyError):
        VibeConfigSchema()


def test_unknown_theme_falls_back_to_default() -> None:
    config = VibeConfigSchema(theme="totally-unknown-theme")
    assert config.theme == DEFAULT_THEME
