from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import tomllib

import pytest

from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import (
    MissingAPIKeyError,
    RawConfig,
    VibeConfigSchema,
    build_default_orchestrator,
)
from vibe.core.config.builder import ConfigBuilder
from vibe.core.config.layers.agent_profile import AgentProfileLayer
from vibe.core.config.layers.discovered import DiscoveredConfigLayer
from vibe.core.config.patch import AddOperationPatch
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.manager import ToolManager
from vibe.core.trusted_folders import trusted_folders_manager

_DISCOVERED_LAYER_DISABLED = pytest.mark.skip(
    reason="DiscoveredConfigLayer is not yet part of build_default_orchestrator"
)


@pytest.mark.asyncio
async def test_build_default_orchestrator_does_not_validate_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY")

    orchestrator = await build_default_orchestrator()
    await orchestrator.reload()
    assert orchestrator.config.get_active_provider().name == "mistral"

    with pytest.raises(MissingAPIKeyError):
        orchestrator.config.require_active_provider_api_key()


@pytest.mark.asyncio
async def test_build_default_orchestrator_builds_layer_stack_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls = 0
    original_build = ConfigBuilder.build

    async def count_builds(
        self: ConfigBuilder[VibeConfigSchema],
        force_load: bool = False,
        *,
        layer_overrides: Mapping[str, RawConfig] | None = None,
    ) -> VibeConfigSchema:
        nonlocal build_calls
        build_calls += 1
        return await original_build(self, force_load, layer_overrides=layer_overrides)

    monkeypatch.setattr(ConfigBuilder, "build", count_builds)

    await build_default_orchestrator()

    assert build_calls == 1


async def _switch_profile_with_agent_layer(
    manager: AgentManager, orchestrator, name: str
) -> None:
    profile = manager.get_agent(name)
    result = await orchestrator.set_field(
        "",
        profile.overrides,
        reason=f"agent profile changed to {profile.name}",
        target_layer=AgentProfileLayer.NAME,
    )
    assert result == []
    manager.switch_profile(profile.name)


@pytest.mark.asyncio
async def test_build_default_orchestrator_uses_standard_layer_priority(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_working_directory: Path
) -> None:
    user_config_path = config_dir / "config.toml"
    user_config_path.write_text(
        """\
default_agent = "ask"
context_warnings = false
ask_confirmation_on_exit = true
displayed_workdir = "user-only"
""",
        encoding="utf-8",
    )
    project_config_path = tmp_working_directory / ".vibe" / "config.toml"
    project_config_path.parent.mkdir(parents=True)
    project_config_path.write_text(
        """\
default_agent = "plan"
context_warnings = true
ask_confirmation_on_exit = true
""",
        encoding="utf-8",
    )
    trusted_folders_manager.add_trusted(project_config_path.parent)
    monkeypatch.setenv("VIBE_DEFAULT_AGENT", "accept-edits")
    monkeypatch.setenv("VIBE_ASK_CONFIRMATION_ON_EXIT", "false")

    orchestrator = await build_default_orchestrator({"default_agent": "auto-approve"})

    assert orchestrator.config.default_agent == "auto-approve"
    assert orchestrator.config.context_warnings is True
    assert orchestrator.config.ask_confirmation_on_exit is False
    assert orchestrator.config.displayed_workdir == "user-only"

    result = await orchestrator.set_field("/displayed_workdir", "patched")

    assert result == []
    # The user layer is the default write target, so an untargeted write lands
    # there and leaves the trusted project config untouched, even though that
    # layer sits above the user layer for reads.
    with user_config_path.open("rb") as file:
        assert tomllib.load(file)["displayed_workdir"] == "patched"
    with project_config_path.open("rb") as file:
        assert "displayed_workdir" not in tomllib.load(file)
    assert orchestrator.config.displayed_workdir == "patched"


@pytest.mark.asyncio
async def test_build_default_orchestrator_merges_default_models_with_user_models(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        """\
active_model = "custom"

[[providers]]
name = "custom-provider"
api_base = "https://custom.example/v1"
api_key_env_var = ""

[[models]]
name = "custom-model"
provider = "custom-provider"
alias = "custom"
""",
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()

    assert orchestrator.config.active_model == "custom"
    assert set(orchestrator.config.models) >= {"custom", "mistral-medium-3.5", "local"}


@pytest.mark.asyncio
async def test_default_layer_completes_sparse_override_of_default_model(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        """\
active_model = "custom"

[[providers]]
name = "custom-provider"
api_base = "https://custom.example/v1"
api_key_env_var = ""

[[models]]
name = "custom-model"
provider = "custom-provider"
alias = "custom"

[[models]]
alias = "mistral-medium-3.5"
thinking = "off"
""",
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()

    overridden = orchestrator.config.models["mistral-medium-3.5"]
    # thinking is overridden away from the default ("high") by the TOML.
    assert overridden.thinking == "off"
    # name/provider are absent from the TOML; the default layer supplies them.
    assert overridden.name == "mistral-vibe-cli-latest"
    assert overridden.provider == "mistral"

    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    entry = next(m for m in persisted["models"] if m["alias"] == "mistral-medium-3.5")
    assert entry == {"alias": "mistral-medium-3.5", "thinking": "off"}


@pytest.mark.asyncio
async def test_build_default_orchestrator_drops_sparse_leftover_devstral_small(
    config_dir: Path,
) -> None:
    # A sparse leftover of the removed default has no name/provider. DefaultConfigLayer
    # can no longer complete it, so the migration must drop it or load fails.
    config_path = config_dir / "config.toml"
    config_path.write_text(
        'active_model = "devstral-small"\n\n'
        "[[models]]\n"
        'alias = "devstral-small"\n'
        'thinking = "low"\n',
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()

    assert "devstral-small" not in orchestrator.config.models
    assert orchestrator.config.active_model != "devstral-small"
    assert orchestrator.config.get_active_model().alias in orchestrator.config.models

    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted.get("active_model", "") == ""
    assert "devstral-small" not in {
        model.get("alias")
        for model in persisted.get("models", [])
        if isinstance(model, dict)
    }


@pytest.mark.asyncio
async def test_build_default_orchestrator_keeps_complete_custom_devstral_small(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        'active_model = "devstral-small"\n\n'
        "[[models]]\n"
        'name = "devstral-small-latest"\n'
        'provider = "mistral"\n'
        'alias = "devstral-small"\n'
        'thinking = "off"\n',
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()

    assert orchestrator.config.active_model == "devstral-small"
    kept = orchestrator.config.models["devstral-small"]
    assert kept.name == "devstral-small-latest"
    assert kept.provider == "mistral"


@pytest.mark.asyncio
async def test_build_default_orchestrator_accepts_model_mapping(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        """\
active_model = "custom"

[[providers]]
name = "custom-provider"
api_base = "https://custom.example/v1"
api_key_env_var = ""

[models.custom]
name = "custom-model"
provider = "custom-provider"
""",
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()

    assert orchestrator.config.active_model == "custom"
    assert orchestrator.config.get_active_model().name == "custom-model"


@pytest.mark.asyncio
async def test_config_orchestrator_writes_model_mapping_as_legacy_list(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"

    orchestrator = await build_default_orchestrator()
    result = await orchestrator.set_field(
        "/models",
        {"custom": {"name": "custom-model", "provider": "mistral"}},
        reason="internal model mapping update",
    )

    assert result == []
    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["models"] == [
        {"name": "custom-model", "provider": "mistral", "alias": "custom"}
    ]


@pytest.mark.asyncio
async def test_config_orchestrator_deep_merges_model_mapping_updates(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        """\
active_model = "custom"

[[models]]
name = "custom-model"
provider = "mistral"
alias = "custom"
temperature = 0.2
""",
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()
    result = await orchestrator.set_field("/models/custom/thinking", "high")

    assert result == []
    assert orchestrator.config.models["custom"].thinking == "high"
    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["models"] == [
        {
            "name": "custom-model",
            "provider": "mistral",
            "alias": "custom",
            "temperature": 0.2,
            "thinking": "high",
        }
    ]


@pytest.mark.asyncio
async def test_config_orchestrator_can_patch_default_model_thinking(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"

    orchestrator = await build_default_orchestrator()
    result = await orchestrator.set_field("/models/mistral-medium-3.5/thinking", "off")

    assert result == []
    assert orchestrator.config.models["mistral-medium-3.5"].thinking == "off"
    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    entry = next(
        model for model in persisted["models"] if model["alias"] == "mistral-medium-3.5"
    )
    assert entry == {"alias": "mistral-medium-3.5", "thinking": "off"}
    await orchestrator.reload()
    assert orchestrator.config.models["mistral-medium-3.5"].thinking == "off"


@pytest.mark.skip(
    reason="DiscoveredConfigLayer is not yet part of build_default_orchestrator"
)
@pytest.mark.asyncio
async def test_build_default_orchestrator_discovered_layer_sits_below_toml(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('disabled_tools = ["user-tool"]\n', encoding="utf-8")

    orchestrator = await build_default_orchestrator()
    result = await orchestrator.apply_patch(
        [
            AddOperationPatch(
                path="/disabled_tools",
                value=["discovered-tool"],
                target_layer_name=DiscoveredConfigLayer.NAME,
            )
        ],
        reason="discovered tool filtering",
    )

    assert result == []
    assert orchestrator.config.disabled_tools == ["discovered-tool", "user-tool"]


@_DISCOVERED_LAYER_DISABLED
@pytest.mark.asyncio
async def test_tool_defaults_can_be_written_to_discovered_layer() -> None:
    orchestrator = await build_default_orchestrator()
    discovered_tools = ToolManager.discover_tool_defaults()

    result = await orchestrator.set_field(
        "/tools",
        discovered_tools,
        reason="discovered tool defaults",
        target_layer=DiscoveredConfigLayer.NAME,
    )

    assert result == []
    assert orchestrator.config.tools["bash"]["permission"] == ToolPermission.ASK
    assert orchestrator.config.tools["bash"]["default_timeout"] == 300
    assert orchestrator.config.tools["read_file"]["permission"] == ToolPermission.ALWAYS
    assert orchestrator.config.tools["read_file"]["max_read_bytes"] == 51200


@pytest.mark.asyncio
async def test_agent_profile_layer_overrides_runtime_overrides() -> None:
    orchestrator = await build_default_orchestrator({
        "bypass_tool_permissions": False,
        "disabled_tools": ["runtime-disabled"],
    })

    result = await orchestrator.set_field(
        "",
        {"enabled_tools": ["grep"], "tools": {"write_file": {"permission": "never"}}},
        reason="previous agent profile",
        target_layer=AgentProfileLayer.NAME,
    )

    assert result == []
    assert orchestrator.config.enabled_tools == ["grep"]
    assert "write_file" in orchestrator.config.tools

    result = await orchestrator.set_field(
        "",
        {"bypass_tool_permissions": True, "disabled_tools": ["agent-disabled"]},
        reason="agent profile changed",
        target_layer=AgentProfileLayer.NAME,
    )

    assert result == []
    assert orchestrator.config.enabled_tools == []
    assert "write_file" not in orchestrator.config.tools
    assert orchestrator.config.bypass_tool_permissions is True
    assert orchestrator.config.disabled_tools == ["runtime-disabled", "agent-disabled"]


@pytest.mark.asyncio
async def test_agent_manager_switch_profile_updates_agent_profile_layer() -> None:
    orchestrator = await build_default_orchestrator()

    manager = AgentManager(orchestrator)

    await _switch_profile_with_agent_layer(manager, orchestrator, BuiltinAgentName.PLAN)

    assert manager.active_profile.name == BuiltinAgentName.PLAN
    assert orchestrator.config.tools["write_file"]["permission"] == "never"
    assert manager.config.tools["write_file"]["permission"] == "never"

    tool_manager = ToolManager(lambda: orchestrator.config)

    assert tool_manager.get_tool_config("write_file").permission == ToolPermission.NEVER


@pytest.mark.asyncio
async def test_agent_manager_switch_profile_replaces_agent_profile_layer() -> None:
    orchestrator = await build_default_orchestrator()
    manager = AgentManager(orchestrator)

    await _switch_profile_with_agent_layer(manager, orchestrator, BuiltinAgentName.PLAN)

    assert orchestrator.config.tools["write_file"]["permission"] == "never"

    await _switch_profile_with_agent_layer(manager, orchestrator, BuiltinAgentName.ASK)

    assert manager.active_profile.name == BuiltinAgentName.ASK
    assert "write_file" not in orchestrator.config.tools
    assert "write_file" not in manager.config.tools


@pytest.mark.asyncio
async def test_agent_manager_switch_writes_discovered_agent_profile_layer(
    config_dir: Path, tmp_path: Path
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "scout.toml").write_text(
        "\n".join([
            'display_name = "Scout"',
            'description = "Discovery profile"',
            'enabled_tools = ["grep"]',
            "[tools.write_file]",
            'permission = "never"',
        ]),
        encoding="utf-8",
    )
    (agents_dir / "review.toml").write_text(
        "\n".join([
            'display_name = "Review"',
            'description = "Review profile"',
            "bypass_tool_permissions = true",
            'disabled_tools = ["bash"]',
        ]),
        encoding="utf-8",
    )
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'agent_paths = ["{agents_dir.as_posix()}"]\n', encoding="utf-8"
    )
    orchestrator = await build_default_orchestrator()
    manager = AgentManager(orchestrator)

    await _switch_profile_with_agent_layer(manager, orchestrator, "scout")

    assert manager.active_profile.name == "scout"
    assert orchestrator.config.enabled_tools == ["grep"]
    assert orchestrator.config.tools["write_file"]["permission"] == "never"

    await _switch_profile_with_agent_layer(manager, orchestrator, "review")

    assert manager.active_profile.name == "review"
    assert orchestrator.config.enabled_tools == []
    assert "write_file" not in orchestrator.config.tools
    assert orchestrator.config.bypass_tool_permissions is True
    assert orchestrator.config.disabled_tools == ["bash"]


@pytest.mark.asyncio
async def test_build_default_orchestrator_migrates_user_config(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        """\
active_model = "devstral-2"

[[models]]
name = "mistral-vibe-cli-latest"
alias = "devstral-2"
provider = "mistral"
""",
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()

    assert orchestrator.config.active_model == "mistral-medium-3.5"
    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "mistral-medium-3.5"
    assert persisted["models"][0]["alias"] == "mistral-medium-3.5"


@pytest.mark.asyncio
async def test_build_default_orchestrator_migrates_project_config_when_present(
    tmp_working_directory: Path,
) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """\
active_model = "devstral-2"

[[models]]
name = "mistral-vibe-cli-latest"
alias = "devstral-2"
provider = "mistral"
""",
        encoding="utf-8",
    )
    trusted_folders_manager.add_trusted(config_path.parent)

    orchestrator = await build_default_orchestrator()

    assert orchestrator.config.active_model == "mistral-medium-3.5"
    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "mistral-medium-3.5"
    assert persisted["models"][0]["alias"] == "mistral-medium-3.5"


@pytest.mark.asyncio
async def test_build_default_orchestrator_migrates_agent_profiles_from_config_paths(
    config_dir: Path, tmp_path: Path
) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    agent_file = agents_dir / "legacy.toml"
    agent_file.write_text(
        "\n".join([
            'display_name = "Legacy"',
            'disabled_tools = ["bash"]',
            'base_disabled = ["exit_plan_mode", "bash"]',
        ]),
        encoding="utf-8",
    )
    config_path = config_dir / "config.toml"
    config_path.write_text(
        f'agent_paths = ["{agents_dir.as_posix()}"]\n', encoding="utf-8"
    )

    orchestrator = await build_default_orchestrator()
    # Agent profile migration runs at AgentManager construction, not orchestrator build.
    AgentManager(orchestrator)

    with agent_file.open("rb") as file:
        persisted = tomllib.load(file)
    assert "base_disabled" not in persisted
    assert persisted["disabled_tools"] == ["bash", "exit_plan_mode"]
