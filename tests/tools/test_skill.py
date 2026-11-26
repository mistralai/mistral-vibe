from __future__ import annotations

import pytest

from vibe.core.tools.base import BaseToolState, ToolPermission
from vibe.core.tools.builtins.skill import Skill, SkillArgs, SkillToolConfig


@pytest.fixture
def skill_tool() -> Skill:
    return Skill(config_getter=lambda: SkillToolConfig(), state=BaseToolState())


class TestSkillPermission:
    def test_resolve_permission_always_allowed(self, skill_tool: Skill) -> None:
        perm = skill_tool.resolve_permission(SkillArgs(name="my-skill"))

        assert perm is not None
        assert perm.permission == ToolPermission.ALWAYS
        assert perm.required_permissions == []

    def test_non_builtin_skill_is_still_always_allowed(self, skill_tool: Skill) -> None:
        perm = skill_tool.resolve_permission(SkillArgs(name="custom-skill"))

        assert perm is not None
        assert perm.permission == ToolPermission.ALWAYS


class TestSkillMeta:
    def test_tool_name(self) -> None:
        assert Skill.get_name() == "skill"

    def test_description_is_set(self) -> None:
        assert "skill" in Skill.get_full_description().lower()
        assert len(Skill.get_full_description()) > 20
