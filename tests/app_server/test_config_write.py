from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server._config_write import config_write_targets, model_config_write_ops
from vibe.core.config import ModelConfig, build_default_orchestrator
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.trusted_folders import trusted_folders_manager


def _routed_model() -> ModelConfig:
    return ModelConfig(
        name="glm-5.2",
        provider="mistral",
        alias="glm-5-2",
        temperature=1.0,
        thinking="off",
        supports_images=True,
    )


@pytest.mark.asyncio
async def test_config_write_targets_offer_user_project_and_session(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    project_vibe_dir = tmp_working_directory / ".vibe"
    project_vibe_dir.mkdir(parents=True, exist_ok=True)
    (project_vibe_dir / "config.toml").write_text('theme = "project-theme"\n')
    trusted_folders_manager.add_trusted(project_vibe_dir)

    orchestrator = await build_default_orchestrator()

    assert config_write_targets(orchestrator) == [
        "user-toml",
        "project-toml",
        "overrides",
    ]


@pytest.mark.asyncio
async def test_config_write_targets_skip_untrusted_project(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    project_vibe_dir = tmp_working_directory / ".vibe"
    project_vibe_dir.mkdir(parents=True, exist_ok=True)
    (project_vibe_dir / "config.toml").write_text('theme = "project-theme"\n')

    orchestrator = await build_default_orchestrator()

    assert config_write_targets(orchestrator) == ["user-toml", "overrides"]


@pytest.mark.asyncio
async def test_config_write_targets_skip_undiscovered_project(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    orchestrator = await build_default_orchestrator()

    assert config_write_targets(orchestrator) == ["user-toml", "overrides"]


def test_picking_the_default_model_is_written_as_an_empty_alias() -> None:
    """*Prepare*: A configuration offering two models, pinned to one.
    *Do*: Pick the default, which the picker sends as an empty alias.
    *Assert*: It writes an empty `/active_model` instead of being refused as an
    unknown model. Empty is how this configuration says "follow the default",
    and writing it is the only way to unpin a session.
    """
    config = build_test_vibe_config(
        models=[
            ModelConfig(name="model-a", provider="mistral", alias="alpha"),
            ModelConfig(name="model-b", provider="mistral", alias="beta"),
        ],
        active_model="alpha",
    )

    ops = model_config_write_ops(config, model_alias="", reasoning_effort=None)

    assert [(op.path, op.value) for op in ops] == [("/active_model", "")]


def test_the_default_gets_the_thinking_written_with_it() -> None:
    """*Prepare*: A configuration pinned to a model that is not the default.
    *Do*: Unpin and set a thinking level in one write.
    *Assert*: The level lands on the default -- the model this write leaves
    active -- and not on the one being unpinned. Nothing sends both today, but
    this function's promise is that the two travel together.
    """
    config = build_test_vibe_config(
        models=[
            ModelConfig(name="model-a", provider="mistral", alias="alpha"),
            ModelConfig(name="model-b", provider="mistral", alias="beta"),
        ],
        active_model="beta",
    )
    default = config.resolve_default_model_alias()

    ops = model_config_write_ops(config, model_alias="", reasoning_effort="low")

    assert [(op.path, op.value) for op in ops] == [
        ("/active_model", ""),
        (f"/models/{default}/thinking", "low"),
    ]


@pytest.mark.asyncio
async def test_a_profile_owned_model_is_not_session_writable() -> None:
    """*Prepare*: A session on a model an agent profile declares, as Lean does.
    *Do*: Ask whether the session may scope that model's fields, write the
    level it would have written anyway, then leave the profile.
    *Assert*: It may not, and leaving is survivable. Lean sets the level for
    the model it declares, so the level is the profile's to give.
    """
    from vibe.app_server._session_model import (
        model_thinking_is_session_writable,
        set_session_reasoning_effort_override,
    )
    from vibe.core.agents.models import ASK, LEAN
    from vibe.core.agents.registry import apply_profile_overrides
    from vibe.core.config.layers.agent_profile import AgentProfileLayer
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[
            DefaultConfigLayer(schema=VibeConfigSchema),
            overrides,
            AgentProfileLayer(),
        ],
        default_layer_resolver=lambda: overrides,
    )
    apply_profile_overrides(orchestrator, LEAN.overrides)
    owned = orchestrator.config.get_active_model()

    # Do
    writable = model_thinking_is_session_writable(orchestrator, owned.alias)
    failures = await set_session_reasoning_effort_override(
        orchestrator, "low", reason="test"
    )
    apply_profile_overrides(orchestrator, ASK.overrides)

    # Assert
    assert writable is False
    assert failures == []
    assert owned.alias not in orchestrator.config.models
    assert orchestrator.config.get_active_model().thinking is not None


@pytest.mark.asyncio
async def test_a_model_the_session_owns_stays_session_writable() -> None:
    """*Prepare*: A session whose active model comes from the default layer.
    *Do*: Ask whether the session may scope that model's fields, and write.
    *Assert*: It may, and the level is the one the session runs.
    """
    from vibe.app_server._session_model import (
        model_thinking_is_session_writable,
        set_session_reasoning_effort_override,
    )
    from vibe.core.config.layers.agent_profile import AgentProfileLayer
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[
            DefaultConfigLayer(schema=VibeConfigSchema),
            overrides,
            AgentProfileLayer(),
        ],
        default_layer_resolver=lambda: overrides,
    )
    alias = orchestrator.config.get_active_model().alias

    # Do
    writable = model_thinking_is_session_writable(orchestrator, alias)
    failures = await set_session_reasoning_effort_override(
        orchestrator, "off", reason="test"
    )

    # Assert
    assert writable is True
    assert failures == []
    assert orchestrator.config.models[alias].thinking == "off"


@pytest.mark.asyncio
async def test_a_profile_that_sets_no_level_leaves_it_to_the_session() -> None:
    """*Prepare*: A profile that pins a model without saying how hard it thinks.
    *Do*: Scope a level to the session.
    *Assert*: It lands and it is what the model runs. Pinning a model is not
    claiming its level, and refusing here would drop the pick into the user's
    own configuration, where every other session reads it.
    """
    from vibe.app_server._session_model import (
        model_thinking_is_session_writable,
        set_session_reasoning_effort_override,
    )
    from vibe.core.agents.registry import apply_profile_overrides
    from vibe.core.config.layers.agent_profile import AgentProfileLayer
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[
            DefaultConfigLayer(schema=VibeConfigSchema),
            overrides,
            AgentProfileLayer(),
        ],
        default_layer_resolver=lambda: overrides,
    )
    apply_profile_overrides(
        orchestrator,
        {
            "active_model": "profile-pinned",
            "models": [
                {
                    "name": "profile-pinned-model",
                    "provider": "mistral",
                    "alias": "profile-pinned",
                }
            ],
        },
    )
    alias = orchestrator.config.get_active_model().alias

    # Do
    writable = model_thinking_is_session_writable(orchestrator, alias)
    failures = await set_session_reasoning_effort_override(
        orchestrator, "off", reason="test"
    )

    # Assert
    assert writable is True
    assert failures == []
    assert orchestrator.config.models[alias].thinking == "off"


@pytest.mark.asyncio
async def test_a_session_level_survives_the_layer_that_declared_its_model() -> None:
    """*Prepare*: A session on a model only a runtime layer declares, as a
    routed model is, with a level scoped to the session.
    *Do*: Drop the layer that declared it, as a refresh that stops routing does.
    *Assert*: The configuration still loads. The override is written through the
    same translation a `config/write` uses, so it carries the model's identity
    rather than becoming an entry holding one field.
    """
    from vibe.app_server._session_model import set_session_reasoning_effort_override
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    routed = OverridesLayer(
        data={
            "active_model": "routed",
            "models": [
                {"name": "routed-model", "provider": "mistral", "alias": "routed"}
            ],
        },
        name="routed-source",
    )
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[DefaultConfigLayer(schema=VibeConfigSchema), routed, overrides],
        default_layer_resolver=lambda: overrides,
    )
    failures = await set_session_reasoning_effort_override(
        orchestrator, "low", reason="test"
    )
    assert failures == []
    assert orchestrator.config.models["routed"].thinking == "low"

    # Do
    orchestrator.replace_or_append_layer("routed-source", OverridesLayer(data={}))
    orchestrator.rebuild()

    # Assert
    assert orchestrator.config.models["routed"].thinking == "low"
    assert orchestrator.config.models["routed"].provider == "mistral"
