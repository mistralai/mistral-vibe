from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SkillMetadata(BaseModel):
    model_config = {"populate_by_name": True}

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$",
        description="Skill identifier. Lowercase letters, numbers, and hyphens only.",
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="What this skill does and when to use it.",
    )
    license: str | None = Field(
        default=None, description="License name or reference to a bundled license file."
    )
    compatibility: str | None = Field(
        default=None,
        max_length=500,
        description="Environment requirements (intended product, system packages, etc.).",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary key-value mapping for additional metadata.",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        validation_alias="allowed-tools",
        description="Space-delimited list of pre-approved tools (experimental).",
    )

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def parse_allowed_tools(cls, v: str | list[str] | None) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return v.split()
        return list(v)

    @field_validator("metadata", mode="before")
    @classmethod
    def normalize_metadata(cls, v: dict[str, Any] | None) -> dict[str, str]:
        if v is None:
            return {}
        return {str(k): str(val) for k, val in v.items()}


class SkillInfo(BaseModel):
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    skill_path: Path

    model_config = {"arbitrary_types_allowed": True}

    @property
    def skill_dir(self) -> Path:
        return self.skill_path.parent.resolve()

    @classmethod
    def from_metadata(cls, meta: SkillMetadata, skill_path: Path) -> SkillInfo:
        return cls(
            name=meta.name,
            description=meta.description,
            license=meta.license,
            compatibility=meta.compatibility,
            metadata=meta.metadata,
            allowed_tools=meta.allowed_tools,
            skill_path=skill_path.resolve(),
        )
