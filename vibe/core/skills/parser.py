from __future__ import annotations

import re
from typing import Any

import yaml


class SkillParseError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


FM_BOUNDARY = re.compile(r"^-{3,}\s*$", re.MULTILINE)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
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
