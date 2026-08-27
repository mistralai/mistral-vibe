from __future__ import annotations

from vibe.core.skills.builtins.skill_creator import SKILL as SKILL_CREATOR_SKILL
from vibe.core.skills.builtins.vibe import SKILL as VIBE_SKILL
from vibe.core.skills.models import SkillInfo

BUILTIN_SKILLS: dict[str, SkillInfo] = {
    skill.name: skill for skill in [VIBE_SKILL, SKILL_CREATOR_SKILL]
}
