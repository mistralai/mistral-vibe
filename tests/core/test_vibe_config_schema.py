from __future__ import annotations

from pathlib import Path

import pytest

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
vim_keybindings = true
api_timeout = 300.0
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

    class VibeConfig(VibeConfigSchema):
        pass

    layer = UserConfigLayer(path=toml_path, name="user-toml")
    orchestrator = await ConfigOrchestrator[VibeConfig].create(
        schema=VibeConfig, layers=[layer]
    )
    config = orchestrator.config

    assert config.vim_keybindings is True
    assert config.api_timeout == 300.0
    assert config.active_model == "codestral"
    assert config.models[0].alias == "codestral"
    assert "bash" in config.disabled_tools
    assert config.default_agent == "plan"
    assert "search" in config.enabled_skills
    assert config.enable_otel is True
