from __future__ import annotations

import json
from pathlib import Path

import keyring
from pydantic import ValidationError
import pytest

from vibe.core.config import MissingAPIKeyError, ModelConfig, ProviderConfig
from vibe.core.config.vibe_schema import VibeConfigSchema

_ROUTED_TEST_ALIAS = "target-testing-model-alias"
_ROUTED_TEST_MODEL = ModelConfig(
    name="target-testing-model-name",
    provider="mistral",
    alias=_ROUTED_TEST_ALIAS,
    input_price=0.0,
    output_price=0.0,
    supports_images=False,
)
_ROUTED_TEST_MODEL_JSON = _ROUTED_TEST_MODEL.model_dump_json()


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


def test_active_model_defaults_to_unpinned_sentinel() -> None:
    config = VibeConfigSchema()

    # The empty string is the "unpinned/default" sentinel; it is preserved (not
    # rewritten to a concrete alias) so the routing experiment can target it.
    assert config.active_model == ""


def test_default_agent_is_accept_edits() -> None:
    assert VibeConfigSchema().default_agent == "accept-edits"


def test_unpinned_active_model_resolves_to_default_model() -> None:
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    config = VibeConfigSchema()

    assert config.get_active_model().alias == DEFAULT_ACTIVE_MODEL_CONFIG.alias


def test_unpinned_active_model_falls_back_to_first_configured_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    with caplog.at_level("WARNING"):
        config = VibeConfigSchema.model_validate({"active_model": "", "models": models})

    assert config.active_model == ""
    assert config.get_active_model().alias == "a"
    assert config.resolve_default_model_alias() == "a"
    assert "is not in your configured models" not in caplog.text


def test_routed_default_model_resolves_when_unpinned() -> None:
    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
    })

    assert config.active_model == ""
    assert config.resolve_default_model_alias() == _ROUTED_TEST_ALIAS
    assert config.get_active_model().alias == _ROUTED_TEST_ALIAS


def test_routed_default_model_ignored_when_pinned() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    config = VibeConfigSchema.model_validate({
        "active_model": "a",
        "routed_default_model": "b",
        "models": models,
    })

    # An explicit pin always wins; the routed default only fills the unpinned case.
    assert config.active_model == "a"
    assert config.get_active_model().alias == "a"


def test_unknown_routed_default_model_falls_back_to_default() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    config = VibeConfigSchema.model_validate({
        "active_model": "",
        "routed_default_model": "does-not-exist",
        "models": models,
    })

    # A routed alias that names no configured model is ignored, not raised.
    assert config.resolve_default_model_alias() == "a"
    assert config.get_active_model().alias == "a"


def test_gated_model_absent_from_defaults() -> None:
    config = VibeConfigSchema()

    assert _ROUTED_TEST_ALIAS not in config.models


def test_routed_model_config_is_injected() -> None:
    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
    })

    assert config.models.get(_ROUTED_TEST_ALIAS) == _ROUTED_TEST_MODEL


def test_routed_model_display_name_is_injected() -> None:
    routed = _ROUTED_TEST_MODEL.model_copy(
        update={"display_name": "glm-5.2 (Mistral Hosted)"}
    )
    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": routed.model_dump_json(),
    })

    assert config.get_active_model().display_name == "glm-5.2 (Mistral Hosted)"


def test_model_display_name_is_optional() -> None:
    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
    })

    assert config.get_active_model().display_name is None


def test_routed_model_not_injected_without_config() -> None:
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    config = VibeConfigSchema(routed_default_model=_ROUTED_TEST_ALIAS)

    assert _ROUTED_TEST_ALIAS not in config.models
    assert config.get_active_model().alias == DEFAULT_ACTIVE_MODEL_CONFIG.alias


def test_routed_model_not_injected_on_alias_mismatch() -> None:
    mismatched = ModelConfig(
        name="x", provider="mistral", alias="other-alias"
    ).model_dump_json()
    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": mismatched,
    })

    assert _ROUTED_TEST_ALIAS not in config.models
    assert "other-alias" not in config.models


def test_malformed_routed_model_config_string_fails_open() -> None:
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": "not-json",
    })

    assert config.routed_model_config is None
    assert _ROUTED_TEST_ALIAS not in config.models
    assert config.get_active_model().alias == DEFAULT_ACTIVE_MODEL_CONFIG.alias


def test_gated_model_not_injected_without_routing() -> None:
    config = VibeConfigSchema.model_validate({"active_model": ""})

    assert _ROUTED_TEST_ALIAS not in config.models


def test_routed_model_available_but_not_active_when_pinned_to_other() -> None:
    config = VibeConfigSchema.model_validate({
        "active_model": "devstral-small",
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
    })

    assert config.active_model == "devstral-small"
    assert config.get_active_model().alias == "devstral-small"
    assert _ROUTED_TEST_ALIAS in config.models
    assert config.resolve_default_model_alias() == _ROUTED_TEST_ALIAS


def test_gated_model_injected_when_pinned_to_routed_alias() -> None:
    config = VibeConfigSchema.model_validate({
        "active_model": _ROUTED_TEST_ALIAS,
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
    })

    assert config.active_model == _ROUTED_TEST_ALIAS
    assert config.models.get(_ROUTED_TEST_ALIAS) == _ROUTED_TEST_MODEL
    assert config.get_active_model().alias == _ROUTED_TEST_ALIAS


def test_user_model_entry_wins_over_routed_injection() -> None:
    # A user-defined model for the routed alias is never overwritten by injection.
    user_model = ModelConfig(
        name="my-own-model", provider="mistral", alias=_ROUTED_TEST_ALIAS
    )
    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
        "models": [user_model],
    })

    assert config.models[_ROUTED_TEST_ALIAS].name == "my-own-model"


def test_sparse_user_entry_merges_routed_model_fields() -> None:
    # A sparse user entry (e.g. only thinking) must still pick up the routed
    # model's other fields (temperature, supports_images, …) so that writing a
    # single field does not silently drop routed defaults on reload.
    sparse_model = ModelConfig(
        name=_ROUTED_TEST_MODEL.name,
        provider="mistral",
        alias=_ROUTED_TEST_ALIAS,
        thinking="medium",
    )
    config = VibeConfigSchema.model_validate({
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
        "models": [sparse_model],
    })

    merged = config.models[_ROUTED_TEST_ALIAS]
    assert merged.thinking == "medium"
    assert merged.input_price == _ROUTED_TEST_MODEL.input_price
    assert merged.supports_images == _ROUTED_TEST_MODEL.supports_images


_EXTRA_MODEL_ALIAS = "extra-dropdown-model"
_EXTRA_MODEL = ModelConfig(
    name="extra-dropdown-model-name",
    provider="mistral",
    alias=_EXTRA_MODEL_ALIAS,
    input_price=0.0,
    output_price=0.0,
    supports_images=False,
)
_EXTRA_MODELS_JSON = f"[{_EXTRA_MODEL.model_dump_json()}]"


def test_routed_extra_model_added_to_dropdown_without_becoming_default() -> None:
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    config = VibeConfigSchema.model_validate({
        "active_model": "",
        "routed_extra_models": _EXTRA_MODELS_JSON,
    })

    # Present in the dropdown ...
    assert _EXTRA_MODEL_ALIAS in config.models
    assert _EXTRA_MODEL_ALIAS in config.available_models()
    # ... but never the default: it does not touch routed_default_model.
    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias
    assert config.get_active_model().alias == DEFAULT_ACTIVE_MODEL_CONFIG.alias


def test_routed_extra_models_accepts_multiple_definitions() -> None:
    second = ModelConfig(name="second-name", provider="mistral", alias="second-extra")
    payload = f"[{_EXTRA_MODEL.model_dump_json()},{second.model_dump_json()}]"

    config = VibeConfigSchema.model_validate({"routed_extra_models": payload})

    assert _EXTRA_MODEL_ALIAS in config.models
    assert "second-extra" in config.models


def test_routed_extra_model_becomes_default_when_named_by_routed_default() -> None:
    # New flag adds the model; old flag names it as default. The two compose:
    # the model is both in the dropdown and promoted to default.
    config = VibeConfigSchema.model_validate({
        "active_model": "",
        "routed_extra_models": _EXTRA_MODELS_JSON,
        "routed_default_model": _EXTRA_MODEL_ALIAS,
    })

    assert _EXTRA_MODEL_ALIAS in config.models
    assert config.resolve_default_model_alias() == _EXTRA_MODEL_ALIAS
    assert config.get_active_model().alias == _EXTRA_MODEL_ALIAS


def test_routed_extra_models_compose_with_routed_model_config() -> None:
    # Old add-and-default flag and the new add-only flag stay independent: the
    # routed default model is the default, the extra model is only in the dropdown.
    config = VibeConfigSchema.model_validate({
        "active_model": "",
        "routed_default_model": _ROUTED_TEST_ALIAS,
        "routed_model_config": _ROUTED_TEST_MODEL_JSON,
        "routed_extra_models": _EXTRA_MODELS_JSON,
    })

    assert config.models.get(_ROUTED_TEST_ALIAS) == _ROUTED_TEST_MODEL
    assert _EXTRA_MODEL_ALIAS in config.models
    assert config.resolve_default_model_alias() == _ROUTED_TEST_ALIAS
    assert config.get_active_model().alias == _ROUTED_TEST_ALIAS


def test_malformed_routed_extra_models_fails_open() -> None:
    config = VibeConfigSchema.model_validate({"routed_extra_models": "not-json"})

    assert config.routed_extra_models == []
    assert _EXTRA_MODEL_ALIAS not in config.models


def test_invalid_routed_extra_model_entries_are_dropped() -> None:
    payload = json.dumps([
        {"not": "a model"},
        json.loads(_EXTRA_MODEL.model_dump_json()),
    ])

    config = VibeConfigSchema.model_validate({"routed_extra_models": payload})

    aliases = {model.alias for model in config.routed_extra_models}
    assert aliases == {_EXTRA_MODEL_ALIAS}
    assert _EXTRA_MODEL_ALIAS in config.models


def test_empty_alias_extra_model_is_not_injected_and_keeps_default() -> None:
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    # An empty alias would collide with the unpinned active_model sentinel ("")
    # and hijack the default. It must be dropped, not injected.
    empty_alias = ModelConfig(name="", provider="mistral", alias="")
    config = VibeConfigSchema.model_validate({
        "active_model": "",
        "routed_extra_models": f"[{empty_alias.model_dump_json()}]",
    })

    assert "" not in config.models
    assert config.resolve_default_model_alias() == DEFAULT_ACTIVE_MODEL_CONFIG.alias
    assert config.get_active_model().alias == DEFAULT_ACTIVE_MODEL_CONFIG.alias


def test_user_model_entry_wins_over_routed_extra_injection() -> None:
    user_model = ModelConfig(
        name="my-own-model", provider="mistral", alias=_EXTRA_MODEL_ALIAS
    )
    config = VibeConfigSchema.model_validate({
        "routed_extra_models": _EXTRA_MODELS_JSON,
        "models": [user_model],
    })

    assert config.models[_EXTRA_MODEL_ALIAS].name == "my-own-model"


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


def test_allowed_models_filters_available_models() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
        ModelConfig(name="model-c", provider="mistral", alias="c"),
    ]
    config = VibeConfigSchema.model_validate({
        "models": models,
        "allowed_models": ["a", "c"],
    })

    assert set(config.available_models()) == {"a", "c"}
    assert set(config.models) == {"a", "b", "c"}


def test_unmatched_allowed_model_emits_validation_warning() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    config = VibeConfigSchema.model_validate({
        "models": models,
        "allowed_models": ["a", "does-not-exist"],
    })

    assert len(config.validation_warnings) == 1
    assert "does-not-exist" in config.validation_warnings[0]


def test_matched_allowed_models_emit_no_warning() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    config = VibeConfigSchema.model_validate({
        "models": models,
        "allowed_models": ["a", "b*"],
    })

    assert config.validation_warnings == ()


def test_disallowed_active_model_pin_falls_back_to_allowed_model() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    # "b" is configured but excluded by the allowlist, so it must never resolve
    # as the active model even though it is pinned.
    config = VibeConfigSchema.model_validate({
        "active_model": "b",
        "models": models,
        "allowed_models": ["a"],
    })

    active = config.get_active_model()
    assert active.alias == "a"
    assert active.alias in config.available_models()


def test_allowed_active_model_pin_is_respected() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    config = VibeConfigSchema.model_validate({
        "active_model": "b",
        "models": models,
        "allowed_models": ["a", "b"],
    })

    assert config.get_active_model().alias == "b"


def test_default_alias_resolves_within_allowed_models() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    config = VibeConfigSchema.model_validate({
        "active_model": "",
        "models": models,
        "allowed_models": ["b"],
    })

    assert config.resolve_default_model_alias() == "b"
    assert config.get_active_model().alias == "b"


def test_allowed_models_matching_nothing_falls_back_to_all() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="a"),
        ModelConfig(name="model-b", provider="mistral", alias="b"),
    ]
    config = VibeConfigSchema.model_validate({
        "active_model": "a",
        "models": models,
        "allowed_models": ["does-not-exist"],
    })

    assert set(config.available_models()) == {"a", "b"}
    assert config.get_active_model().alias == "a"
    assert len(config.validation_warnings) == 1
    assert "does-not-exist" in config.validation_warnings[0]


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


def test_theme_is_preserved_for_the_client_to_interpret() -> None:
    config = VibeConfigSchema(theme="totally-unknown-theme")
    assert config.theme == "totally-unknown-theme"


def test_log_level_defaults_to_none() -> None:
    schema = VibeConfigSchema()
    assert schema.log_level is None


def test_log_level_normalizes_case() -> None:
    schema = VibeConfigSchema(log_level="debug")
    assert schema.log_level == "DEBUG"


def test_log_level_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        VibeConfigSchema(log_level="VERBOSE")


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_log_level_accepts_canonical_levels(level: str) -> None:
    assert VibeConfigSchema(log_level=level).log_level == level
