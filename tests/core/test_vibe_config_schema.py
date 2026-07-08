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
from vibe.core.config._settings import VibeConfig
from vibe.core.config.vibe_schema import VibeConfigSchema


def test_vibe_config_schema_covers_all_vibe_config_fields() -> None:
    legacy_fields = set(VibeConfig.model_fields.keys())
    schema_fields = set(VibeConfigSchema.model_fields.keys())
    missing = legacy_fields - schema_fields
    assert not missing, (
        f"VibeConfigSchema is missing {len(missing)} field(s) that exist in VibeConfig: "
        f"{sorted(missing)}. "
        f"When you add a new field to VibeConfig, also add it to VibeConfigSchema "
        f"(vibe/core/config/vibe_schema.py) with the appropriate merge annotation."
    )


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
experimental_vibe_code_project_picker_enabled = true

[[models]]
alias = "codestral"
name = "codestral-latest"
provider = "mistral"
"""
    )

    from vibe.core.config.layers.user import UserConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    class VibeConfig(VibeConfigSchema):
        pass

    layer = UserConfigLayer(path=toml_path)
    orchestrator = await ConfigOrchestrator[VibeConfig].create(
        schema=VibeConfig, layers=[layer], default_layer_resolver=lambda: layer
    )
    config = orchestrator.config

    assert config.disable_welcome_banner_animation is True
    assert config.api_timeout == 300.0
    assert config.api_retry_max_elapsed_time == 120.0
    assert config.active_model == "codestral"
    assert config.models[0].alias == "codestral"
    assert "bash" in config.disabled_tools
    assert config.default_agent == "plan"
    assert "search" in config.enabled_skills
    assert config.enable_otel is True
    assert config.experimental_vibe_code_project_picker_enabled is True


def test_duplicate_model_alias_raises() -> None:
    with pytest.raises(ValueError, match="Duplicate alias"):
        VibeConfigSchema(
            models=[
                ModelConfig(name="model-a", provider="mistral", alias="same"),
                ModelConfig(name="model-b", provider="mistral", alias="same"),
            ]
        )


def test_unknown_active_model_falls_back_to_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        config = VibeConfigSchema(active_model="does-not-exist")

    assert config.active_model == config.models[0].alias
    assert config.get_active_model().alias == config.models[0].alias
    assert (
        "Active model 'does-not-exist' is not in your configured models" in caplog.text
    )


def test_known_active_model_is_not_overridden(caplog: pytest.LogCaptureFixture) -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    with caplog.at_level("WARNING"):
        config = VibeConfigSchema(active_model="b", models=models)
    assert config.active_model == "b"
    assert "is not in your configured models" not in caplog.text


def test_no_models_raises() -> None:
    with pytest.raises(ValueError, match="No models are configured"):
        VibeConfigSchema(models=[])


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
