from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from tests.skills.conftest import create_skill
from vibe.core.skills.builtins import BUILTIN_SKILLS
from vibe.core.skills.manager import SkillManager


class TestBuiltinSkills:
    def test_vibe_skill_is_registered(self) -> None:
        assert "vibe" in BUILTIN_SKILLS

    def test_vibe_skill_has_no_path(self) -> None:
        assert BUILTIN_SKILLS["vibe"].skill_path is None

    def test_vibe_skill_has_inline_prompt(self) -> None:
        assert BUILTIN_SKILLS["vibe"].prompt

    def test_vibe_skill_pins_readme_url_to_running_version(self) -> None:
        from vibe import __version__

        prompt = BUILTIN_SKILLS["vibe"].prompt
        assert "__VIBE_VERSION__" not in prompt
        assert (
            f"https://github.com/mistralai/mistral-vibe/blob/v{__version__}/README.md"
            in prompt
        )

    def test_vibe_skill_references_user_docs_url(self) -> None:
        assert (
            "https://docs.mistral.ai/vibe/code/overview"
            in BUILTIN_SKILLS["vibe"].prompt
        )

    def test_discovers_builtin_skills(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("vibe.core.skills.manager.BUILTIN_SKILLS", BUILTIN_SKILLS)
        config = build_test_vibe_config()
        manager = SkillManager(lambda: config)

        assert "vibe" in manager.available_skills

    def test_user_skill_cannot_override_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.skills.manager.BUILTIN_SKILLS", BUILTIN_SKILLS)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        create_skill(skills_dir, "vibe", "Custom vibe override")

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        assert "vibe" in manager.available_skills
        assert (
            manager.available_skills["vibe"].description
            == BUILTIN_SKILLS["vibe"].description
        )
