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
_KEY_VALUE_LINE = re.compile(
    r"^(?P<key>[^:'\"]*[^\s:'\"]):(?P<gap>\s+)(?P<value>.*?)(?P<comment>\s+#.*)?$"
)
_COLON_SEPARATOR = re.compile(r":(\s|$)")
_COMMENT = re.compile(r"(^|\s)#")
_NON_PLAIN_VALUE_STARTS = ("'", '"', "|", ">", "!", "&", "*")


def _is_prose_continuation(line: str) -> bool:
    stripped = line.strip()
    return not (
        _COLON_SEPARATOR.search(stripped)
        or _COMMENT.search(stripped)
        or stripped.startswith("-")
    )


def _quote_rejected_value(lines: list[str], error: yaml.YAMLError) -> list[str] | None:
    # Other harnesses accept prose like `description: Use when: X`, which strict YAML
    # rejects. Only quote the plain value the parser points at, never valid content.
    mark = error.problem_mark if isinstance(error, yaml.MarkedYAMLError) else None
    if mark is None or mark.line >= len(lines):
        return None
    match = _KEY_VALUE_LINE.match(lines[mark.line])
    if match is None or mark.column < match.start("value"):
        return None
    value = match["value"].rstrip()
    if value.startswith(_NON_PLAIN_VALUE_STARTS) or not _COLON_SEPARATOR.search(value):
        return None
    # A line inside a multi-line flow collection would swallow its brackets or comma.
    if (
        value.endswith(",")
        or value.count("[") != value.count("]")
        or value.count("{") != value.count("}")
    ):
        return None

    key = match["key"]
    owner_column = len(key) - len(key.lstrip(" -"))
    end = mark.line + 1
    while end < len(lines) and (
        not lines[end].strip()
        or len(lines[end]) - len(lines[end].lstrip(" ")) > owner_column
    ):
        end += 1
    while end > mark.line + 1 and not lines[end - 1].strip():
        end -= 1
    continuation = lines[mark.line + 1 : end]
    if continuation and (
        match["comment"] or not all(map(_is_prose_continuation, continuation))
    ):
        return None

    text = "\n".join([value, *(line.rstrip() for line in continuation)])
    quoted = "'" + text.replace("'", "''") + "'"
    repaired = f"{key}:{match['gap']}{quoted}{match['comment'] or ''}"
    return [*lines[: mark.line], *repaired.split("\n"), *lines[end:]]


def _load_frontmatter(yaml_content: str) -> Any:
    try:
        return yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        original_error = e

    lines = yaml_content.replace("\r\n", "\n").split("\n")
    error: yaml.YAMLError = original_error
    while (lines := _quote_rejected_value(lines, error)) is not None:
        try:
            return yaml.safe_load("\n".join(lines))
        except yaml.YAMLError as e:
            error = e
    raise SkillParseError(
        f"Invalid YAML frontmatter: {original_error}"
    ) from original_error


def parse_skill_markdown(content: str) -> tuple[dict[str, Any], str]:
    content = content.lstrip("\ufeff")
    splits = FM_BOUNDARY.split(content, 2)
    if len(splits) < 3 or splits[0].strip():  # noqa: PLR2004
        raise SkillParseError(
            "Missing or invalid YAML frontmatter (metadata section must start and end with ---)"
        )

    yaml_content = splits[1]
    markdown_body = splits[2]

    frontmatter = _load_frontmatter(yaml_content)

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
