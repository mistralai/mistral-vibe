from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.mock.utils import collect_result
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillInfo
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError
from vibe.core.tools.builtins.skill import (
    Skill,
    SkillArgs,
    SkillResult,
    SkillToolConfig,
)
from vibe.core.tools.permissions import PermissionScope


def _make_skill_dir(
    tmp_path: Path,
    name: str = "my-skill",
    description: str = "A test skill",
    body: str = "## Instructions\n\nDo the thing.",
    extra_files: list[str] | None = None,
) -> SkillInfo:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    for f in extra_files or []:
        file_path = skill_dir / f
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"content of {f}", encoding="utf-8")

    return SkillInfo(
        name=name, description=description, skill_path=skill_dir / "SKILL.md"
    )


def _make_skill_manager(skills: dict[str, SkillInfo]) -> SkillManager:
    manager = MagicMock(spec=SkillManager)
    manager.available_skills = skills
    manager.get_skill.side_effect = lambda n: skills.get(n)
    return manager


def _make_ctx(skill_manager: SkillManager | None = None) -> InvokeContext:
    return InvokeContext(tool_call_id="test-call", skill_manager=skill_manager)


@pytest.fixture
def skill_tool() -> Skill:
    return Skill(config_getter=lambda: SkillToolConfig(), state=BaseToolState())


class TestSkillRun:
    @pytest.mark.asyncio
    async def test_loads_skill_content(self, tmp_path: Path, skill_tool: Skill) -> None:
        info = _make_skill_dir(tmp_path, body="Follow these steps:\n1. Do A\n2. Do B")
        manager = _make_skill_manager({"my-skill": info})
        ctx = _make_ctx(manager)

        result = await collect_result(skill_tool.run(SkillArgs(name="my-skill"), ctx))

        assert isinstance(result, SkillResult)
        assert result.name == "my-skill"
        assert "Follow these steps:" in result.content
        assert "1. Do A" in result.content
        assert '<skill_content name="my-skill">' in result.content
        assert "# Skill: my-skill" in result.content
        assert "</skill_content>" in result.content

    @pytest.mark.asyncio
    async def test_lists_bundled_files(self, tmp_path: Path, skill_tool: Skill) -> None:
        info = _make_skill_dir(
            tmp_path, extra_files=["scripts/run.sh", "references/guide.md"]
        )
        manager = _make_skill_manager({"my-skill": info})
        ctx = _make_ctx(manager)

        result = await collect_result(skill_tool.run(SkillArgs(name="my-skill"), ctx))

        assert "<skill_files>" in result.content
        assert "<file>scripts/run.sh</file>" in result.content
        assert "<file>references/guide.md</file>" in result.content
        assert f"<file>{info.skill_dir / 'scripts/run.sh'}</file>" not in result.content

    @pytest.mark.asyncio
    async def test_excludes_skill_md_from_file_list(
        self, tmp_path: Path, skill_tool: Skill
    ) -> None:
        info = _make_skill_dir(tmp_path, extra_files=["helper.py"])
        manager = _make_skill_manager({"my-skill": info})
        ctx = _make_ctx(manager)

        result = await collect_result(skill_tool.run(SkillArgs(name="my-skill"), ctx))

        assert "SKILL.md" not in result.content.split("<skill_files>")[1]
        assert "helper.py" in result.content

    @pytest.mark.asyncio
    async def test_caps_file_list_at_ten(
        self, tmp_path: Path, skill_tool: Skill
    ) -> None:
        files = [f"file{i:02d}.txt" for i in range(15)]
        info = _make_skill_dir(tmp_path, extra_files=files)
        manager = _make_skill_manager({"my-skill": info})
        ctx = _make_ctx(manager)

        result = await collect_result(skill_tool.run(SkillArgs(name="my-skill"), ctx))

        file_section = result.content.split("<skill_files>")[1].split("</skill_files>")[
            0
        ]
        assert file_section.count("<file>") == 10

    @pytest.mark.asyncio
    async def test_empty_skill_directory(
        self, tmp_path: Path, skill_tool: Skill
    ) -> None:
        info = _make_skill_dir(tmp_path)
        manager = _make_skill_manager({"my-skill": info})
        ctx = _make_ctx(manager)

        result = await collect_result(skill_tool.run(SkillArgs(name="my-skill"), ctx))

        assert "<skill_files>\n\n</skill_files>" in result.content

    @pytest.mark.asyncio
    async def test_returns_skill_dir(self, tmp_path: Path, skill_tool: Skill) -> None:
        info = _make_skill_dir(tmp_path)
        manager = _make_skill_manager({"my-skill": info})
        ctx = _make_ctx(manager)

        result = await collect_result(skill_tool.run(SkillArgs(name="my-skill"), ctx))

        assert result.skill_dir == str(info.skill_dir)

    @pytest.mark.asyncio
    async def test_includes_base_directory(
        self, tmp_path: Path, skill_tool: Skill
    ) -> None:
        info = _make_skill_dir(tmp_path)
        manager = _make_skill_manager({"my-skill": info})
        ctx = _make_ctx(manager)

        result = await collect_result(skill_tool.run(SkillArgs(name="my-skill"), ctx))

        assert f"Base directory for this skill: {info.skill_dir}" in result.content


class TestSkillErrors:
    @pytest.mark.asyncio
    async def test_no_context(self, skill_tool: Skill) -> None:
        with pytest.raises(ToolError, match="Skill manager not available"):
            await collect_result(skill_tool.run(SkillArgs(name="test"), ctx=None))

    @pytest.mark.asyncio
    async def test_no_skill_manager(self, skill_tool: Skill) -> None:
        ctx = _make_ctx(skill_manager=None)
        with pytest.raises(ToolError, match="Skill manager not available"):
            await collect_result(skill_tool.run(SkillArgs(name="test"), ctx=ctx))

    @pytest.mark.asyncio
    async def test_skill_not_found(self, skill_tool: Skill) -> None:
        manager = _make_skill_manager({"alpha": MagicMock(), "beta": MagicMock()})
        ctx = _make_ctx(manager)

        with pytest.raises(ToolError, match='Skill "missing" not found'):
            await collect_result(skill_tool.run(SkillArgs(name="missing"), ctx=ctx))

    @pytest.mark.asyncio
    async def test_skill_not_found_lists_available(self, skill_tool: Skill) -> None:
        manager = _make_skill_manager({"alpha": MagicMock(), "beta": MagicMock()})
        ctx = _make_ctx(manager)

        with pytest.raises(ToolError, match="alpha, beta"):
            await collect_result(skill_tool.run(SkillArgs(name="missing"), ctx=ctx))

    @pytest.mark.asyncio
    async def test_unreadable_skill_file(
        self, tmp_path: Path, skill_tool: Skill
    ) -> None:
        info = SkillInfo(
            name="broken",
            description="Broken skill",
            skill_path=tmp_path / "nonexistent" / "SKILL.md",
        )
        manager = _make_skill_manager({"broken": info})
        ctx = _make_ctx(manager)

        with pytest.raises(ToolError, match="Cannot load skill file"):
            await collect_result(skill_tool.run(SkillArgs(name="broken"), ctx=ctx))


class TestSkillPermission:
    def test_resolve_permission_returns_file_pattern(self, skill_tool: Skill) -> None:
        perm = skill_tool.resolve_permission(SkillArgs(name="my-skill"))

        assert perm is not None
        assert len(perm.required_permissions) == 1
        assert perm.required_permissions[0].scope == PermissionScope.FILE_PATTERN
        assert perm.required_permissions[0].invocation_pattern == "my-skill"
        assert perm.required_permissions[0].session_pattern == "my-skill"


class TestSkillMeta:
    def test_tool_name(self) -> None:
        assert Skill.get_name() == "skill"

    def test_description_is_set(self) -> None:
        assert "skill" in Skill.description.lower()
        assert len(Skill.description) > 20
