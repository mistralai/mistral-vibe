from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.conftest import build_test_vibe_config
from vibe.core.config import VibeConfig


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a temporary skills directory."""
    skills = tmp_path / "skills"
    skills.mkdir()
    return skills


@pytest.fixture
def skill_config(skills_dir: Path) -> VibeConfig:
    return build_test_vibe_config(
        system_prompt_id="tests",
        include_project_context=False,
        skill_paths=[skills_dir],
    )


def create_skill(
    skills_dir: Path,
    name: str,
    description: str = "A test skill",
    *,
    license: str | None = None,
    compatibility: str | None = None,
    metadata: dict[str, str] | None = None,
    allowed_tools: str | None = None,
    user_invocable: bool | None = None,
    body: str = "## Instructions\n\nTest instructions here.",
) -> Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter: dict[str, object] = {"name": name, "description": description}
    if license:
        frontmatter["license"] = license
    if compatibility:
        frontmatter["compatibility"] = compatibility
    if metadata:
        frontmatter["metadata"] = metadata
    if allowed_tools:
        frontmatter["allowed-tools"] = allowed_tools
    if user_invocable is not None:
        frontmatter["user-invocable"] = user_invocable

    yaml_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    content = f"---\n{yaml_str}---\n\n{body}"

    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")

    return skill_dir
