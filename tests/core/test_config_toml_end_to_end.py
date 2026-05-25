from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError
import pytest

from vibe.core.config.schema import (
    ConfigFragment,
    ConfigSchema,
    WithReplaceMerge,
    WithUnionMerge,
)


class ModelsFragment(ConfigFragment):
    active_model: Annotated[str, WithReplaceMerge()] = "default-model"
    models: Annotated[list[dict[str, str]], WithUnionMerge(merge_key="alias")] = Field(
        default_factory=list
    )
    providers: Annotated[list[dict[str, str]], WithUnionMerge(merge_key="name")] = (
        Field(default_factory=list)
    )


class MinimalSchema(ConfigSchema):
    models: ModelsFragment = Field(default_factory=ModelsFragment)


@pytest.mark.asyncio
async def test_toml_to_typed_config_end_to_end(tmp_working_directory: Path) -> None:
    toml_path = tmp_working_directory / "config.toml"
    toml_path.write_text(
        """\
[models]
active_model = "mistral-large"

[[models.models]]
alias = "mistral-large"
provider = "mistral"

[[models.providers]]
name = "mistral"
api_base = "https://api.mistral.ai/v1"
"""
    )

    from vibe.core.config.layers.user import UserConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    layer = UserConfigLayer(path=toml_path, name="user-toml")
    orchestrator = await ConfigOrchestrator.create(schema=MinimalSchema, layers=[layer])

    assert orchestrator.config.models.active_model == "mistral-large"
    assert orchestrator.config.models.models == [
        {"alias": "mistral-large", "provider": "mistral"}
    ]
    # Immutability (frozen schema)
    with pytest.raises(ValidationError, match="frozen"):
        orchestrator.config.models = ModelsFragment()  # type: ignore[misc]

    # Reload picks up changes
    toml_path.write_text(
        """\
[models]
active_model = "codestral"
"""
    )
    await orchestrator.reload()
    assert orchestrator.config.models.active_model == "codestral"
