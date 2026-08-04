from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field
import pytest

from vibe.core.config import (
    ConfigDefinitionError,
    ConfigFragment,
    ConfigSchema,
    WithConcatMerge,
    WithReplaceMerge,
    WithShallowMerge,
    WithUnionMerge,
)


class ToolSettings(BaseModel):
    enabled: bool = True


class ModelDefinition(BaseModel):
    alias: str
    provider: str


class ToolsFragment(ConfigFragment):
    disabled_tools: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list
    )
    tool_settings: Annotated[dict[str, ToolSettings], WithShallowMerge()] = Field(
        default_factory=dict
    )


class ModelsFragment(ConfigFragment):
    default_model: Annotated[str, WithReplaceMerge()] = "devstral-small"
    available_models: Annotated[
        list[ModelDefinition], WithUnionMerge(merge_key="alias")
    ] = Field(default_factory=list)


class MiniVibeConfigSchema(ConfigSchema):
    active_model: Annotated[str, WithReplaceMerge()] = "devstral-2"
    models: ModelsFragment = Field(default_factory=ModelsFragment)
    tools: ToolsFragment = Field(default_factory=ToolsFragment)


def test_config_fragment_accepts_merge_aware_fields() -> None:
    fragment = ToolsFragment(
        disabled_tools=["bash"], tool_settings={"search": ToolSettings(enabled=False)}
    )

    assert fragment.disabled_tools == ["bash"]
    assert fragment.tool_settings["search"].enabled is False


def test_config_fragment_rejects_plain_fields() -> None:
    with pytest.raises(
        ConfigDefinitionError, match="BrokenFragment.name must declare merge metadata"
    ):

        class BrokenFragment(ConfigFragment):
            name: str = "bash"


def test_scenario_mini_vibe_config_schema() -> None:
    config = MiniVibeConfigSchema(
        active_model="codestral-latest",
        models=ModelsFragment(
            available_models=[
                ModelDefinition(alias="devstral-small", provider="mistral"),
                ModelDefinition(alias="codestral-latest", provider="mistral"),
            ]
        ),
        tools=ToolsFragment(disabled_tools=["bash"]),
    )

    assert config.active_model == "codestral-latest"
    assert config.models.default_model == "devstral-small"
    assert [model.alias for model in config.models.available_models] == [
        "devstral-small",
        "codestral-latest",
    ]
    assert config.tools.disabled_tools == ["bash"]


def test_config_schema_rejects_plain_top_level_fields() -> None:
    with pytest.raises(
        ConfigDefinitionError,
        match="BrokenSchema.active_model must declare merge metadata",
    ):

        class BrokenSchema(ConfigSchema):
            active_model: str = "devstral-2"


def test_config_schema_rejects_plain_nested_models() -> None:
    with pytest.raises(
        ConfigDefinitionError,
        match="BrokenSchema.tools must declare merge metadata or use a ConfigFragment subclass",
    ):

        class BrokenSchema(ConfigSchema):
            tools: ToolSettings = Field(default_factory=ToolSettings)


def test_config_schema_rejects_merge_metadata_on_fragments() -> None:
    with pytest.raises(
        ConfigDefinitionError,
        match="BrokenSchema.tools is a ConfigFragment field and must not declare merge metadata",
    ):

        class BrokenSchema(ConfigSchema):
            tools: Annotated[ToolsFragment, WithReplaceMerge()] = Field(
                default_factory=ToolsFragment
            )


def test_config_schema_rejects_optional_fragments() -> None:
    with pytest.raises(
        ConfigDefinitionError,
        match="BrokenSchema.tools must declare merge metadata or use a ConfigFragment subclass",
    ):

        class BrokenSchema(ConfigSchema):
            tools: ToolsFragment | None = None
