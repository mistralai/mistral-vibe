from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from tests.skills.conftest import create_skill
from vibe.core.config import VibeConfig
from vibe.core.skills.manager import SkillManager


@pytest.fixture
def config() -> VibeConfig:
    return build_test_vibe_config(
        system_prompt_id="tests", include_project_context=False
    )


@pytest.fixture
def skill_manager(config: VibeConfig) -> SkillManager:
    return SkillManager(lambda: config)


class TestSkillManagerDiscovery:
    def test_discovers_no_skills_when_directory_empty(
        self, skill_manager: SkillManager
    ) -> None:
        assert skill_manager.available_skills == {}

    def test_discovers_skill_from_skill_paths(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "test-skill", "A test skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        assert "test-skill" in manager.available_skills
        assert manager.available_skills["test-skill"].description == "A test skill"

    def test_discovers_multiple_skills(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "skill-one", "First skill")
        create_skill(skills_dir, "skill-two", "Second skill")
        create_skill(skills_dir, "skill-three", "Third skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        assert len(manager.available_skills) == 3
        assert "skill-one" in manager.available_skills
        assert "skill-two" in manager.available_skills
        assert "skill-three" in manager.available_skills

    def test_ignores_directories_without_skill_md(self, skills_dir: Path) -> None:
        # Create a directory that's not a skill
        not_a_skill = skills_dir / "not-a-skill"
        not_a_skill.mkdir()
        (not_a_skill / "README.md").write_text("Not a skill")

        # Create a valid skill
        create_skill(skills_dir, "valid-skill", "A valid skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert "valid-skill" in skills
        assert "not-a-skill" not in skills

    def test_ignores_files_in_skills_directory(self, skills_dir: Path) -> None:
        # Create a file in the skills directory (not a directory)
        (skills_dir / "not-a-directory.md").write_text("Just a file")

        # Create a valid skill
        create_skill(skills_dir, "valid-skill", "A valid skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert "valid-skill" in skills


class TestSkillManagerParsing:
    def test_parses_all_skill_fields(self, skills_dir: Path) -> None:
        create_skill(
            skills_dir,
            "full-skill",
            "A skill with all fields",
            license="MIT",
            compatibility="Requires git",
            metadata={"author": "Test Author", "version": "1.0"},
            allowed_tools="bash read_file",
        )

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("full-skill")
        assert skill is not None
        assert skill.name == "full-skill"
        assert skill.description == "A skill with all fields"
        assert skill.license == "MIT"
        assert skill.compatibility == "Requires git"
        assert skill.metadata == {"author": "Test Author", "version": "1.0"}
        assert skill.allowed_tools == ["bash", "read_file"]

    def test_sets_correct_skill_path(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "test-skill", "A test skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("test-skill")
        assert skill is not None
        assert skill.skill_path == skills_dir / "test-skill" / "SKILL.md"
        assert skill.skill_dir == skills_dir / "test-skill"

    def test_skips_skill_with_invalid_frontmatter(self, skills_dir: Path) -> None:
        # Create an invalid skill
        invalid_skill_dir = skills_dir / "invalid-skill"
        invalid_skill_dir.mkdir()
        (invalid_skill_dir / "SKILL.md").write_text("No frontmatter here")

        # Create a valid skill
        create_skill(skills_dir, "valid-skill", "A valid skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert "valid-skill" in skills
        assert "invalid-skill" not in skills

    def test_skips_skill_with_missing_required_fields(self, skills_dir: Path) -> None:
        # Create skill missing description
        missing_desc_dir = skills_dir / "missing-desc"
        missing_desc_dir.mkdir()
        (missing_desc_dir / "SKILL.md").write_text("---\nname: missing-desc\n---\n")

        # Create a valid skill
        create_skill(skills_dir, "valid-skill", "A valid skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert "valid-skill" in skills


class TestSkillManagerSearchPaths:
    def test_discovers_from_multiple_skill_paths(self, tmp_path: Path) -> None:
        # Create two separate skill directories
        skills_dir_1 = tmp_path / "skills1"
        skills_dir_1.mkdir()
        create_skill(skills_dir_1, "skill-from-dir1", "Skill from directory 1")

        skills_dir_2 = tmp_path / "skills2"
        skills_dir_2.mkdir()
        create_skill(skills_dir_2, "skill-from-dir2", "Skill from directory 2")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir_1, skills_dir_2],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 2
        assert "skill-from-dir1" in skills
        assert "skill-from-dir2" in skills

    def test_first_discovered_wins_for_duplicates(self, tmp_path: Path) -> None:
        # Create two directories with the same skill name
        skills_dir_1 = tmp_path / "skills1"
        skills_dir_1.mkdir()
        create_skill(skills_dir_1, "duplicate-skill", "First version")

        skills_dir_2 = tmp_path / "skills2"
        skills_dir_2.mkdir()
        create_skill(skills_dir_2, "duplicate-skill", "Second version")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir_1, skills_dir_2],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert skills["duplicate-skill"].description == "First version"

    def test_ignores_nonexistent_skill_paths(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        create_skill(skills_dir, "valid-skill", "A valid skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir, tmp_path / "nonexistent"],
        )
        manager = SkillManager(lambda: config)

        assert len(manager.available_skills) == 1
        assert "valid-skill" in manager.available_skills


class TestSkillManagerGetSkill:
    def test_returns_skill_by_name(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "test-skill", "A test skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("test-skill")
        assert skill is not None
        assert skill.name == "test-skill"

    def test_returns_none_for_unknown_skill(self, skill_manager: SkillManager) -> None:
        assert skill_manager.get_skill("nonexistent-skill") is None


class TestSkillManagerFiltering:
    def test_enabled_skills_filters_to_only_enabled(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "skill-a", "Skill A")
        create_skill(skills_dir, "skill-b", "Skill B")
        create_skill(skills_dir, "skill-c", "Skill C")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
            enabled_skills=["skill-a", "skill-c"],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 2
        assert "skill-a" in skills
        assert "skill-b" not in skills
        assert "skill-c" in skills

    def test_disabled_skills_excludes_disabled(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "skill-a", "Skill A")
        create_skill(skills_dir, "skill-b", "Skill B")
        create_skill(skills_dir, "skill-c", "Skill C")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
            disabled_skills=["skill-b"],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 2
        assert "skill-a" in skills
        assert "skill-b" not in skills
        assert "skill-c" in skills

    def test_enabled_skills_takes_precedence_over_disabled(
        self, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "skill-a", "Skill A")
        create_skill(skills_dir, "skill-b", "Skill B")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
            enabled_skills=["skill-a"],
            disabled_skills=["skill-a"],  # Should be ignored
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert "skill-a" in skills

    def test_glob_pattern_matching(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "search-code", "Search code")
        create_skill(skills_dir, "search-docs", "Search docs")
        create_skill(skills_dir, "other-skill", "Other skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
            enabled_skills=["search-*"],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 2
        assert "search-code" in skills
        assert "search-docs" in skills
        assert "other-skill" not in skills

    def test_regex_pattern_matching(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "skill-v1", "Skill v1")
        create_skill(skills_dir, "skill-v2", "Skill v2")
        create_skill(skills_dir, "other-skill", "Other skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
            enabled_skills=["re:skill-v\\d+"],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 2
        assert "skill-v1" in skills
        assert "skill-v2" in skills
        assert "other-skill" not in skills

    def test_get_skill_respects_filtering(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "enabled-skill", "Enabled")
        create_skill(skills_dir, "disabled-skill", "Disabled")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
            disabled_skills=["disabled-skill"],
        )
        manager = SkillManager(lambda: config)

        assert manager.get_skill("enabled-skill") is not None
        assert manager.get_skill("disabled-skill") is None


class TestSkillUserInvocable:
    def test_user_invocable_defaults_to_true(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "default-skill", "A default skill")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("default-skill")
        assert skill is not None
        assert skill.user_invocable is True

    def test_user_invocable_can_be_set_to_false(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "hidden-skill", "A hidden skill", user_invocable=False)

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("hidden-skill")
        assert skill is not None
        assert skill.user_invocable is False

    def test_user_invocable_can_be_explicitly_set_to_true(
        self, skills_dir: Path
    ) -> None:
        create_skill(
            skills_dir, "explicit-skill", "An explicit skill", user_invocable=True
        )

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("explicit-skill")
        assert skill is not None
        assert skill.user_invocable is True

    def test_mixed_user_invocable_skills(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "visible-skill", "Visible", user_invocable=True)
        create_skill(skills_dir, "hidden-skill", "Hidden", user_invocable=False)
        create_skill(skills_dir, "default-skill", "Default")

        config = build_test_vibe_config(
            system_prompt_id="tests",
            include_project_context=False,
            skill_paths=[skills_dir],
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 3
        assert skills["visible-skill"].user_invocable is True
        assert skills["hidden-skill"].user_invocable is False
        assert skills["default-skill"].user_invocable is True
