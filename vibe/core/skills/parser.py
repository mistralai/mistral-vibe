from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import yaml

from vibe.utils.io import read_safe

OPENAI_SKILL_METADATA_FILENAME = "openai.yaml"
_OPENAI_SKILL_METADATA_DIRNAME = "agents"
_OPENAI_SKILL_METADATA_LABEL = (
    f"{_OPENAI_SKILL_METADATA_DIRNAME}/{OPENAI_SKILL_METADATA_FILENAME}"
)


# Pinned upstream schema and default semantics:
# https://github.com/openai/codex/blob/430d26b543b219049192de559987b8cf506efacf/codex-rs/ext/skills/src/loader/metadata.rs#L27-L56
# https://github.com/openai/codex/blob/430d26b543b219049192de559987b8cf506efacf/codex-rs/skills/src/model.rs#L22-L28
# Unknown policy keys are treated as invalid so a typo cannot silently make an
# intended explicit-only skill available to the model.
class OpenAISkillPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allow_implicit_invocation: bool | None = None
    products: list[str] = Field(default_factory=list)


class OpenAISkillMetadata(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    policy: OpenAISkillPolicy | None = None

    @property
    def allows_implicit_invocation(self) -> bool:
        if self.policy is None or self.policy.allow_implicit_invocation is None:
            return True
        return self.policy.allow_implicit_invocation

    @property
    def has_unhandled_fields(self) -> bool:
        """Whether the file contains metadata Vibe preserves but does not apply."""
        return bool(self.model_extra) or bool(
            self.policy is not None and self.policy.products
        )


class SkillParseError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


FM_BOUNDARY = re.compile(r"^-{3,}\s*$", re.MULTILINE)


def parse_skill_markdown(content: str) -> tuple[dict[str, Any], str]:
    content = content.lstrip("\ufeff")
    splits = FM_BOUNDARY.split(content, 2)
    if len(splits) < 3 or splits[0].strip():  # noqa: PLR2004
        raise SkillParseError(
            "Missing or invalid YAML frontmatter (metadata section must start and end with ---)"
        )

    yaml_content = splits[1]
    markdown_body = splits[2]

    try:
        frontmatter = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise SkillParseError(f"Invalid YAML frontmatter: {e}") from e

    if frontmatter is None:
        frontmatter = {}

    if not isinstance(frontmatter, dict):
        raise SkillParseError("YAML frontmatter must be a mapping/dictionary")

    return frontmatter, markdown_body


def openai_skill_metadata_path(skill_path: Path) -> Path:
    return (
        skill_path.parent
        / _OPENAI_SKILL_METADATA_DIRNAME
        / OPENAI_SKILL_METADATA_FILENAME
    )


def load_openai_skill_metadata(
    skill_path: Path, *, root: Path | None = None
) -> OpenAISkillMetadata | None:
    """Load one skill's optional OpenAI metadata.

    Local skills, native plugins, and compatibility adapters call this at their
    separate ingestion boundaries because each owns a different diagnostic type.
    Filename resolution, parsing, and optional plugin-root containment stay here.
    """
    metadata_path = openai_skill_metadata_path(skill_path)
    if not metadata_path.is_file():
        return None
    try:
        resolved_metadata_path = metadata_path.resolve(strict=True)
        if root is not None and not resolved_metadata_path.is_relative_to(
            root.resolve()
        ):
            raise SkillParseError(
                f"{_OPENAI_SKILL_METADATA_LABEL} must resolve inside the plugin root"
            )
        content = read_safe(resolved_metadata_path, raise_on_error=True).text
    except OSError as e:
        raise SkillParseError(f"Cannot read {_OPENAI_SKILL_METADATA_LABEL}: {e}") from e
    return parse_openai_skill_metadata(content)


def parse_openai_skill_metadata(content: str) -> OpenAISkillMetadata:
    try:
        metadata = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise SkillParseError(f"Invalid {_OPENAI_SKILL_METADATA_LABEL}: {e}") from e

    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise SkillParseError(
            f"{_OPENAI_SKILL_METADATA_LABEL} must be a mapping/dictionary"
        )

    try:
        return OpenAISkillMetadata.model_validate(metadata)
    except ValidationError as e:
        raise SkillParseError(f"Invalid {_OPENAI_SKILL_METADATA_LABEL}: {e}") from e
