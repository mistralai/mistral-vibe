from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server._effect_output_cap import (
    EFFECT_OUTPUT_TEXT_MAX_CHARS,
    TRUNCATION_MARKER,
)
from vibe.app_server._history_projection import project_message_history
from vibe.app_server._projection import project_config_view
from vibe.app_server.models import (
    AgentStatsSnapshot,
    CompletedEffectState,
    PublicEffectEntry,
)
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


def test_persisted_huge_tool_output_is_capped() -> None:
    history = project_message_history(
        "session-1",
        [
            LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[
                    ToolCall(
                        id="tool-1", function=FunctionCall(name="task", arguments="{}")
                    )
                ],
            ),
            LLMMessage(
                role=Role.tool,
                name="task",
                tool_call_id="tool-1",
                content="x" * (EFFECT_OUTPUT_TEXT_MAX_CHARS * 2),
            ),
        ],
        None,
    )

    effect = next(entry for entry in history if isinstance(entry, PublicEffectEntry))
    assert isinstance(effect.state, CompletedEffectState)
    assert effect.state.output_text == (
        f"{'x' * EFFECT_OUTPUT_TEXT_MAX_CHARS}{TRUNCATION_MARKER}"
    )


def test_config_view_reports_the_configured_default_agent() -> None:
    config = project_config_view(build_test_vibe_config(default_agent="ask"))

    assert config.default_agent == "ask"
    assert config.model_dump(mode="json", by_alias=True)["defaultAgent"] == "ask"


@pytest.mark.parametrize("enabled", [True, False])
def test_config_view_projects_cursor_blink(enabled: bool) -> None:
    config = project_config_view(build_test_vibe_config(cursor_blink=enabled))

    assert config.model_dump(mode="json", by_alias=True)["cursorBlink"] is enabled
    legacy_payload = config.model_dump(mode="json", by_alias=True)
    del legacy_payload["cursorBlink"]
    assert type(config).model_validate(legacy_payload).cursor_blink is True


def test_config_view_projects_the_tls_trust_policy() -> None:
    config = project_config_view(build_test_vibe_config(enable_system_trust_store=True))

    assert (
        config.model_dump(mode="json", by_alias=True)["enableSystemTrustStore"] is True
    )


def test_snapshot_session_cost_discounts_cached_tokens() -> None:
    snapshot = AgentStatsSnapshot(
        session_prompt_tokens=1_000_000,
        session_completion_tokens=0,
        session_cached_tokens=400_000,
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
    )
    # 600k * $1/M + 400k * $0.1/M = $0.64
    assert snapshot.session_cost == pytest.approx(0.64)


def test_snapshot_session_cost_bills_cached_at_input_rate_when_unset() -> None:
    snapshot = AgentStatsSnapshot(
        session_prompt_tokens=1_000_000,
        session_completion_tokens=0,
        session_cached_tokens=400_000,
        input_price_per_million=1.0,
    )
    assert snapshot.session_cost == pytest.approx(1.0)


def test_snapshot_session_cost_never_negative_when_cached_exceeds_prompt() -> None:
    snapshot = AgentStatsSnapshot(
        session_prompt_tokens=100_000,
        session_completion_tokens=0,
        session_cached_tokens=190_000,
        input_price_per_million=1.0,
        cached_input_price_per_million=0.1,
    )
    assert snapshot.session_cost == pytest.approx(0.01)


def test_model_config_view_defaults_thinking_levels_for_older_clients() -> None:
    # A payload from an older server lacks thinkingLevels; the view's default
    # keeps such clients rendering the five (ADR 0014).
    from vibe.app_server.config import THINKING_LEVELS, ModelConfigView

    view = ModelConfigView.model_validate({
        "name": "model-a",
        "alias": "alpha",
        "thinking": "off",
        "supportsImages": False,
        "displayName": "alpha",
    })

    assert list(view.thinking_levels) == list(THINKING_LEVELS)
    assert view.auto_compact_threshold is None


def test_config_view_carries_each_model_compaction_threshold() -> None:
    from vibe.core.config import ModelConfig

    models = [
        ModelConfig(
            name="model-a",
            provider="mistral",
            alias="alpha",
            max_context_length=100_000,
        ),
        ModelConfig(
            name="model-b",
            provider="mistral",
            alias="beta",
            max_context_length=100_000,
            auto_compact_threshold=50_000,
        ),
    ]
    config = project_config_view(
        build_test_vibe_config(models=models, active_model="alpha")
    )

    assert config.active_model.auto_compact_threshold == 80_000
    assert {model.alias: model.auto_compact_threshold for model in config.models} == {
        "alpha": 80_000,
        "beta": 50_000,
    }


def test_config_view_default_alias_never_follows_the_active_model() -> None:
    """``default_model_alias`` names the configured default, never the pin.

    It used to be read off the active model unless the caller passed
    ``active_model_pinned``, so any projection that omitted the flag -- every
    unified-harness runtime snapshot -- advertised the user's own pinned model
    as the "Default (currently ...)" hint.
    """
    from vibe.core.config import ModelConfig

    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
    ]
    pinned_config = build_test_vibe_config(models=models, active_model="beta")

    pinned = project_config_view(pinned_config, active_model_pinned=True)
    # A caller that forgets the flag still must not echo the pin back as the
    # default; only ``active_model_pinned`` itself may differ.
    unflagged = project_config_view(pinned_config)
    unpinned = project_config_view(
        build_test_vibe_config(models=models, active_model="")
    )

    assert pinned.active_model.alias == "beta"
    assert pinned.default_model_alias == "alpha"
    assert pinned.default_model_display_name == "alpha"
    assert unflagged.default_model_alias == "alpha"
    assert unpinned.active_model.alias == "alpha"
    assert unpinned.default_model_alias == "alpha"


@pytest.mark.parametrize("active_model", ["text", "vision"])
def test_config_view_reports_file_image_delivery_per_model(active_model: str) -> None:
    from vibe.core.config import ModelConfig

    config = build_test_vibe_config(
        models=[
            ModelConfig(
                name="text-model",
                provider="mistral",
                alias="text",
                supports_images=False,
            ),
            ModelConfig(
                name="vision-model",
                provider="mistral",
                alias="vision",
                supports_images=True,
            ),
        ],
        active_model=active_model,
    )

    view = project_config_view(config, image_fallback=True)
    delivery_by_alias = {model.alias: model.image_delivery for model in view.models}

    assert delivery_by_alias == {"text": "resource_link", "vision": "native"}
    assert view.active_model.image_delivery == delivery_by_alias[active_model]
    serialized = view.model_dump(mode="json", by_alias=True)
    assert serialized["activeModel"]["imageDelivery"] == delivery_by_alias[active_model]
    assert [model["imageDelivery"] for model in serialized["models"]] == [
        "resource_link",
        "native",
    ]


def test_config_view_does_not_advertise_file_delivery_without_runtime_support() -> None:
    from vibe.core.config import ModelConfig

    config = build_test_vibe_config(
        models=[ModelConfig(name="text-model", provider="mistral", alias="text")],
        active_model="text",
    )

    view = project_config_view(config)

    assert view.active_model.image_delivery is None
    assert view.models[0].image_delivery is None


def test_config_view_image_support_widens_for_a_backend_that_can_fall_back() -> None:
    """``images_supported`` gates the send; it is not the model's own vision.

    The unified backend describes an image for a blind model, so it accepts an
    attachment ``active_model.supports_images`` would reject -- but only when a
    describer is actually reachable. Core's resource-link projection alone is
    not enough: it only exists for a file-backed image, so promising on its
    strength would let a pasted one through to a model that cannot read it.
    """
    from vibe.core.config import ModelConfig

    blind = build_test_vibe_config(
        models=[ModelConfig(name="model-a", provider="mistral", alias="blind")],
        active_model="blind",
    )
    describable = build_test_vibe_config(
        models=[
            ModelConfig(name="model-a", provider="mistral", alias="blind"),
            ModelConfig(
                name="model-b", provider="mistral", alias="sees", supports_images=True
            ),
        ],
        active_model="blind",
    )
    sees = build_test_vibe_config(
        models=[
            ModelConfig(
                name="model-b", provider="mistral", alias="sees", supports_images=True
            )
        ],
        active_model="sees",
    )

    assert project_config_view(blind).images_supported is False
    assert project_config_view(blind, image_fallback=True).images_supported is False
    assert (
        project_config_view(describable, image_fallback=True).images_supported is True
    )
    assert project_config_view(sees).images_supported is True


@pytest.mark.asyncio
async def test_apply_cached_experiment_variants_sets_routed_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End-to-end for the warm-cache startup path.
    from vibe.app_server._runtime import _apply_cached_experiment_variants
    from vibe.core.config import build_default_orchestrator
    from vibe.core.experiments import cache
    from vibe.core.experiments.active import ExperimentName
    from vibe.core.experiments.models import EvalResponse

    monkeypatch.setattr(cache, "_cache_key", lambda _config: "user-abc")
    orchestrator = await build_default_orchestrator()
    routing = ExperimentName.CLI_MODEL_ROUTING.value
    response = EvalResponse.model_validate({
        "features": {
            routing: {"rules": [{"force": '{"active_model": "magistral-medium"}'}]}
        }
    })
    cache.store_cached_eval_response(orchestrator.config, response)
    assert not orchestrator.config.routed_default_model

    await _apply_cached_experiment_variants(orchestrator)

    assert orchestrator.config.routed_default_model == "magistral-medium"


@pytest.mark.asyncio
async def test_apply_cached_experiment_variants_noop_on_cold_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.app_server._runtime import _apply_cached_experiment_variants
    from vibe.core.config import build_default_orchestrator
    from vibe.core.experiments import cache

    monkeypatch.setattr(cache, "_cache_key", lambda _config: "user-abc")
    orchestrator = await build_default_orchestrator()

    await _apply_cached_experiment_variants(orchestrator)

    assert not orchestrator.config.routed_default_model
