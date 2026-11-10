from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from tests.conftest import build_test_vibe_config
from tests.skills.conftest import create_skill
from tests.skills.registry.conftest import make_item
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillInfo, SkillScope, SkillSource
from vibe.core.skills.registry import _manifest, _store
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.trusted_folders import trusted_folders_manager


@pytest.fixture
def skill_manager(vibe_config: VibeConfigSchema) -> SkillManager:
    return SkillManager(lambda: vibe_config)


class TestSkillManagerDiscovery:
    def test_available_skills_is_frozen(self, skill_manager: SkillManager) -> None:
        frozen_skills = cast(dict[str, object], skill_manager.available_skills)
        with pytest.raises(TypeError):
            frozen_skills["new-skill"] = object()

    def test_discovers_no_skills_when_directory_empty(
        self, skill_manager: SkillManager
    ) -> None:
        assert skill_manager.available_skills == {}

    def test_discovers_skill_from_skill_paths(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "test-skill", "A test skill")

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        assert "test-skill" in manager.available_skills
        assert manager.available_skills["test-skill"].description == "A test skill"

    def test_discovers_multiple_skills(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "skill-one", "First skill")
        create_skill(skills_dir, "skill-two", "Second skill")
        create_skill(skills_dir, "skill-three", "Third skill")

        config = build_test_vibe_config(skill_paths=[skills_dir])
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

        config = build_test_vibe_config(skill_paths=[skills_dir])
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

        config = build_test_vibe_config(skill_paths=[skills_dir])
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
            allowed_tools="bash read",
        )

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("full-skill")
        assert skill is not None
        assert skill.name == "full-skill"
        assert skill.description == "A skill with all fields"
        assert skill.license == "MIT"
        assert skill.compatibility == "Requires git"
        assert skill.metadata == {"author": "Test Author", "version": "1.0"}
        assert skill.allowed_tools == ["bash", "read"]

    def test_sets_correct_skill_path(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "test-skill", "A test skill")

        config = build_test_vibe_config(skill_paths=[skills_dir])
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

        config = build_test_vibe_config(skill_paths=[skills_dir])
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

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert "valid-skill" in skills


class TestSkillManagerSearchPaths:
    def test_discovers_from_vibe_skills_when_cwd_trusted(
        self, tmp_working_directory: Path
    ) -> None:
        trusted_folders_manager.add_trusted(tmp_working_directory)
        vibe_skills = tmp_working_directory / ".vibe" / "skills"
        vibe_skills.mkdir(parents=True)
        create_skill(vibe_skills, "vibe-skill", "Skill from .vibe/skills")

        config = build_test_vibe_config(skill_paths=[])
        manager = SkillManager(lambda: config)

        assert "vibe-skill" in manager.available_skills
        assert (
            manager.available_skills["vibe-skill"].description
            == "Skill from .vibe/skills"
        )

    def test_discovers_from_agents_skills_when_cwd_trusted(
        self, tmp_working_directory: Path
    ) -> None:
        trusted_folders_manager.add_trusted(tmp_working_directory)
        agents_skills = tmp_working_directory / ".agents" / "skills"
        agents_skills.mkdir(parents=True)
        create_skill(agents_skills, "agents-skill", "Skill from .agents/skills")

        config = build_test_vibe_config(skill_paths=[])
        manager = SkillManager(lambda: config)

        assert "agents-skill" in manager.available_skills
        assert (
            manager.available_skills["agents-skill"].description
            == "Skill from .agents/skills"
        )

    def test_discovers_from_both_vibe_and_agents_skills_when_cwd_trusted(
        self, tmp_working_directory: Path
    ) -> None:
        trusted_folders_manager.add_trusted(tmp_working_directory)
        vibe_skills = tmp_working_directory / ".vibe" / "skills"
        agents_skills = tmp_working_directory / ".agents" / "skills"
        vibe_skills.mkdir(parents=True)
        agents_skills.mkdir(parents=True)
        create_skill(vibe_skills, "vibe-only", "From .vibe")
        create_skill(agents_skills, "agents-only", "From .agents")

        config = build_test_vibe_config(skill_paths=[])
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 2
        assert skills["vibe-only"].description == "From .vibe"
        assert skills["agents-only"].description == "From .agents"

    def test_first_discovered_wins_when_same_skill_in_vibe_and_agents(
        self, tmp_working_directory: Path
    ) -> None:
        trusted_folders_manager.add_trusted(tmp_working_directory)
        vibe_skills = tmp_working_directory / ".vibe" / "skills"
        agents_skills = tmp_working_directory / ".agents" / "skills"
        vibe_skills.mkdir(parents=True)
        agents_skills.mkdir(parents=True)
        create_skill(vibe_skills, "shared-skill", "First from .vibe")
        create_skill(agents_skills, "shared-skill", "Second from .agents")

        config = build_test_vibe_config(skill_paths=[])
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert skills["shared-skill"].description == "First from .vibe"

    def test_discovers_from_multiple_skill_paths(self, tmp_path: Path) -> None:
        # Create two separate skill directories
        skills_dir_1 = tmp_path / "skills1"
        skills_dir_1.mkdir()
        create_skill(skills_dir_1, "skill-from-dir1", "Skill from directory 1")

        skills_dir_2 = tmp_path / "skills2"
        skills_dir_2.mkdir()
        create_skill(skills_dir_2, "skill-from-dir2", "Skill from directory 2")

        config = build_test_vibe_config(skill_paths=[skills_dir_1, skills_dir_2])
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

        config = build_test_vibe_config(skill_paths=[skills_dir_1, skills_dir_2])
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert skills["duplicate-skill"].description == "First version"

    def test_ignores_nonexistent_skill_paths(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        create_skill(skills_dir, "valid-skill", "A valid skill")

        config = build_test_vibe_config(
            skill_paths=[skills_dir, tmp_path / "nonexistent"]
        )
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 1
        assert "valid-skill" in skills


class TestSkillManagerGetSkill:
    def test_returns_skill_by_name(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "test-skill", "A test skill")

        config = build_test_vibe_config(skill_paths=[skills_dir])
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
            skill_paths=[skills_dir], enabled_skills=["skill-a", "skill-c"]
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
            skill_paths=[skills_dir], disabled_skills=["skill-b"]
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
            skill_paths=[skills_dir], enabled_skills=["search-*"]
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
            skill_paths=[skills_dir], enabled_skills=["re:skill-v\\d+"]
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
            skill_paths=[skills_dir], disabled_skills=["disabled-skill"]
        )
        manager = SkillManager(lambda: config)

        assert manager.get_skill("enabled-skill") is not None
        assert manager.get_skill("disabled-skill") is None


class TestSkillUserInvocable:
    def test_user_invocable_defaults_to_true(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "default-skill", "A default skill")

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("default-skill")
        assert skill is not None
        assert skill.user_invocable is True

    def test_user_invocable_can_be_set_to_false(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "hidden-skill", "A hidden skill", user_invocable=False)

        config = build_test_vibe_config(skill_paths=[skills_dir])
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

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("explicit-skill")
        assert skill is not None
        assert skill.user_invocable is True

    def test_mixed_user_invocable_skills(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "visible-skill", "Visible", user_invocable=True)
        create_skill(skills_dir, "hidden-skill", "Hidden", user_invocable=False)
        create_skill(skills_dir, "default-skill", "Default")

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skills = manager.available_skills
        assert len(skills) == 3
        assert skills["visible-skill"].user_invocable is True
        assert skills["hidden-skill"].user_invocable is False
        assert skills["default-skill"].user_invocable is True


class TestSkillModelInvocable:
    def test_disable_model_invocation_keeps_explicit_slash_invocation(
        self, skills_dir: Path
    ) -> None:
        create_skill(
            skills_dir,
            "explicit-only",
            disable_model_invocation=True,
            body="Only run explicitly.",
        )
        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("explicit-only")

        assert skill is not None
        assert skill.model_invocable is False
        assert skill.user_invocable is True

    def test_openai_policy_disables_model_invocation(self, skills_dir: Path) -> None:
        skill_dir = create_skill(skills_dir, "openai-explicit-only")
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
        )
        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("openai-explicit-only")

        assert skill is not None
        assert skill.model_invocable is False
        assert skill.user_invocable is True

    @pytest.mark.parametrize(
        "metadata",
        [
            'policy:\n  allow_implicit_invocation: "false"\n',
            "policy:\n  allow_implicit_invocaton: false\n",
        ],
    )
    def test_invalid_openai_policy_keeps_skill_explicit_only(
        self, skills_dir: Path, metadata: str
    ) -> None:
        skill_dir = create_skill(skills_dir, "invalid-openai-policy")
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()
        metadata_path = agents_dir / "openai.yaml"
        metadata_path.write_text(metadata, encoding="utf-8")
        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        skill = manager.get_skill("invalid-openai-policy")

        assert skill is not None
        assert skill.model_invocable is False
        assert skill.user_invocable is True
        assert manager.config_issues[0].file == metadata_path


class TestSkillManagerConfigIssues:
    def test_invalid_frontmatter_records_config_issue(self, skills_dir: Path) -> None:
        broken_dir = skills_dir / "broken-skill"
        broken_dir.mkdir()
        (broken_dir / "SKILL.md").write_text("No frontmatter here")

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        assert len(manager.config_issues) == 1
        issue = manager.config_issues[0]
        assert issue.file == broken_dir / "SKILL.md"
        assert "Failed to load" in issue.message

    def test_multiple_invalid_skills_accumulate_issues(self, skills_dir: Path) -> None:
        broken_dir_a = skills_dir / "broken-skill-a"
        broken_dir_a.mkdir()
        (broken_dir_a / "SKILL.md").write_text("not valid")
        broken_dir = skills_dir / "broken-skill"
        broken_dir.mkdir()
        (broken_dir / "SKILL.md").write_text("not valid")
        create_skill(skills_dir, "good-skill", "Works fine")

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config)

        assert {
            (i.file, i.message.startswith("Failed to load"))
            for i in manager.config_issues
        } == {(broken_dir_a / "SKILL.md", True), (broken_dir / "SKILL.md", True)}
        assert "good-skill" in manager.available_skills


class TestRegistrySkillDiscovery:
    async def _pin_materialized(
        self, *, skill_id: str, name: str, description: str = "does things"
    ) -> None:
        await _store.materialize(
            make_item(skill_id=skill_id, name=name, description=description, version=1),
            name,
        )
        await _manifest.save(
            _manifest.global_manifest_path(),
            SkillManifest(
                skills=[ManifestEntry(name=name, skill_id=skill_id, version=1)]
            ),
        )

    @pytest.mark.asyncio
    async def test_discovers_materialized_registry_pin(self) -> None:
        await self._pin_materialized(skill_id="cid", name="reg-skill")

        config = build_test_vibe_config(experimental_enable_registry_skills=True)
        manager = SkillManager(lambda: config)

        info = manager.available_skills.get("reg-skill")
        assert info is not None
        assert info.source is SkillSource.REGISTRY
        assert info.registry is not None
        assert info.registry.skill_id == "cid"
        assert info.registry.version == 1

    @pytest.mark.asyncio
    async def test_disabled_flag_hides_registry_pins(self) -> None:
        await self._pin_materialized(skill_id="cid", name="reg-skill")

        config = build_test_vibe_config(experimental_enable_registry_skills=False)
        manager = SkillManager(lambda: config)

        assert "reg-skill" not in manager.available_skills

    @pytest.mark.asyncio
    async def test_a_global_file_wins_over_a_global_pin(self, skills_dir: Path) -> None:
        create_skill(skills_dir, "dup", "local one")
        await self._pin_materialized(
            skill_id="cid", name="dup", description="registry one"
        )

        config = build_test_vibe_config(
            skill_paths=[skills_dir], experimental_enable_registry_skills=True
        )
        manager = SkillManager(lambda: config)

        info = manager.available_skills.get("dup")
        assert info is not None
        assert info.source is SkillSource.LOCAL
        assert info.description == "local one"

    @pytest.mark.asyncio
    async def test_unsafe_skill_id_is_skipped(self) -> None:
        await _manifest.save(
            _manifest.global_manifest_path(),
            SkillManifest(
                skills=[ManifestEntry(name="evil", skill_id="../escape", version=1)]
            ),
        )

        config = build_test_vibe_config(experimental_enable_registry_skills=True)
        manager = SkillManager(lambda: config)

        assert "evil" not in manager.available_skills


async def _pin(
    manifest: Path, *, name: str, skill_id: str, description: str, version: int = 1
) -> None:
    await _store.materialize(
        make_item(
            skill_id=skill_id, name=name, description=description, version=version
        ),
        name,
    )
    current = await _manifest.load(manifest)
    entry = ManifestEntry(name=name, skill_id=skill_id, version=version)
    await _manifest.save(manifest, SkillManifest(skills=[*current.skills, entry]))


class TestSkillResolution:
    @pytest.fixture
    def project(self, tmp_working_directory: Path) -> Path:
        trusted_folders_manager.add_trusted(tmp_working_directory)
        return tmp_working_directory

    @pytest.mark.parametrize("registry", [False, True])
    def test_a_sandboxed_sessions_own_project_skill_outranks_the_hosts_copy(
        self, project: Path, registry: bool
    ) -> None:
        create_skill(project / ".vibe" / "skills", "deploy", "host copy")
        sandboxed = SkillInfo(
            name="deploy",
            description="sandbox project",
            skill_path=Path("/sandbox/project/.vibe/skills/deploy/SKILL.md"),
            prompt="# deploy",
            source=SkillSource.LOCAL,
            scope=SkillScope.PROJECT,
        )
        config = build_test_vibe_config(
            skill_paths=[], experimental_enable_registry_skills=registry
        )

        manager = SkillManager(lambda: config, project_skills={"deploy": sandboxed})

        assert manager.available_skills["deploy"] is sandboxed
        listed = [
            (installed.info, installed.active)
            for installed in manager.installed_skills()
            if installed.info.name == "deploy"
        ]
        assert listed == [(sandboxed, True)]

    @pytest.mark.asyncio
    async def test_a_project_pin_outranks_a_global_file(
        self, project: Path, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "deploy", "global file")
        await _pin(
            project / ".vibe" / "skills.toml",
            name="deploy",
            skill_id="cid",
            description="project pin",
        )
        config = build_test_vibe_config(
            skill_paths=[skills_dir], experimental_enable_registry_skills=True
        )

        manager = SkillManager(lambda: config)

        assert manager.available_skills["deploy"].description == "project pin"

    @pytest.mark.asyncio
    async def test_a_project_file_outranks_a_project_pin(self, project: Path) -> None:
        create_skill(project / ".vibe" / "skills", "deploy", "project file")
        await _pin(
            project / ".vibe" / "skills.toml",
            name="deploy",
            skill_id="cid",
            description="project pin",
        )
        config = build_test_vibe_config(
            skill_paths=[], experimental_enable_registry_skills=True
        )

        manager = SkillManager(lambda: config)

        assert manager.available_skills["deploy"].description == "project file"

    @pytest.mark.parametrize(
        ("registry", "winner"), [(True, "project file"), (False, "configured path")]
    )
    def test_a_configured_skill_path_yields_to_the_project_only_with_registry_skills(
        self, project: Path, skills_dir: Path, registry: bool, winner: str
    ) -> None:
        create_skill(skills_dir, "deploy", "configured path")
        create_skill(project / ".vibe" / "skills", "deploy", "project file")
        config = build_test_vibe_config(
            skill_paths=[skills_dir], experimental_enable_registry_skills=registry
        )

        manager = SkillManager(lambda: config)

        assert manager.available_skills["deploy"].description == winner

    def test_without_registry_skills_shadowing_stays_a_debug_line(
        self, project: Path, skills_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        create_skill(skills_dir, "deploy", "configured path")
        create_skill(project / ".vibe" / "skills", "deploy", "project file")
        config = build_test_vibe_config(skill_paths=[skills_dir])

        with caplog.at_level("DEBUG", logger="vibe"):
            SkillManager(lambda: config)

        levels = {r.levelname for r in caplog.records if "shadowed" in r.getMessage()}
        assert levels == {"DEBUG"}

    @pytest.mark.asyncio
    async def test_one_version_pinned_in_both_scopes_is_not_reported(
        self, project: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        for manifest in (
            _manifest.global_manifest_path(),
            project / ".vibe" / "skills.toml",
        ):
            await _pin(manifest, name="deploy", skill_id="cid", description="pin")
        config = build_test_vibe_config(experimental_enable_registry_skills=True)

        with caplog.at_level("DEBUG", logger="vibe"):
            SkillManager(lambda: config)

        assert not [r for r in caplog.records if "shadowed" in r.getMessage()]

    @pytest.mark.asyncio
    async def test_the_first_project_root_wins_a_name_two_of_them_pin(
        self, project: Path, tmp_path: Path
    ) -> None:
        other = tmp_path / "other"
        other.mkdir()
        await _pin(
            project / ".vibe" / "skills.toml",
            name="deploy",
            skill_id="first",
            description="first root",
        )
        await _pin(
            other / ".vibe" / "skills.toml",
            name="deploy",
            skill_id="second",
            description="second root",
        )
        files = HarnessFilesManager(
            sources=("user", "project"), cwd=project, _additional_dirs=(other,)
        )
        config = build_test_vibe_config(experimental_enable_registry_skills=True)

        manager = SkillManager(lambda: config, harness_files=files)

        assert manager.available_skills["deploy"].description == "first root"

    @pytest.mark.asyncio
    async def test_installed_skills_keep_every_pin_and_mark_the_resolved_one(
        self, project: Path, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "deploy", "global file")
        await _pin(
            _manifest.global_manifest_path(),
            name="deploy",
            skill_id="cid",
            description="global pin",
        )
        await _pin(
            project / ".vibe" / "skills.toml",
            name="deploy",
            skill_id="cid",
            description="project pin",
            version=2,
        )
        config = build_test_vibe_config(
            skill_paths=[skills_dir], experimental_enable_registry_skills=True
        )

        manager = SkillManager(lambda: config)

        rows = [
            (row.info.scope, row.info.source, row.info.description, row.active)
            for row in manager.installed_skills()
            if row.info.name == "deploy"
        ]
        assert rows == [
            (SkillScope.PROJECT, SkillSource.REGISTRY, "project pin", True),
            (SkillScope.GLOBAL, SkillSource.LOCAL, "global file", False),
            (SkillScope.GLOBAL, SkillSource.REGISTRY, "global pin", False),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("pinned_as", "resolved_as"),
        [("bar", "bar"), ("Not A Name", "foo")],
        ids=["renamed", "invalid-name"],
    )
    async def test_a_pin_takes_the_name_its_manifest_gives_it(
        self, project: Path, pinned_as: str, resolved_as: str
    ) -> None:
        await _store.materialize(
            make_item(skill_id="cid", name="foo", version=5), "foo"
        )
        await _manifest.save(
            _manifest.global_manifest_path(),
            SkillManifest(
                skills=[ManifestEntry(name=pinned_as, skill_id="cid", version=5)]
            ),
        )
        config = build_test_vibe_config(experimental_enable_registry_skills=True)

        skills = SkillManager(lambda: config).available_skills

        assert list(skills) == [resolved_as]
        registry = skills[resolved_as].registry
        assert registry is not None
        assert (registry.skill_id, registry.version) == ("cid", 5)

    @pytest.mark.asyncio
    async def test_two_pins_of_one_stored_file_resolve_once_as_the_session_loads_it(
        self, project: Path
    ) -> None:
        pytest.importorskip("mistralai_vibe_local_harness.protocol")
        from vibe.app_server._skills import discover_session_skills

        await _store.materialize(
            make_item(skill_id="cid", name="foo", version=5), "foo"
        )
        await _manifest.save(
            _manifest.global_manifest_path(),
            SkillManifest(skills=[ManifestEntry(name="foo", skill_id="cid")]),
        )
        await _manifest.save(
            project / ".vibe" / "skills.toml",
            SkillManifest(skills=[ManifestEntry(name="bar", skill_id="cid")]),
        )
        config = build_test_vibe_config(experimental_enable_registry_skills=True)
        files = HarnessFilesManager(sources=("user", "project"), cwd=project)

        manager = SkillManager(lambda: config, harness_files=files)
        _, projection = discover_session_skills(
            lambda: config,
            harness_files=files,
            plugin_skills={},
            plugin_contexts=[],
            skill_tool_available=True,
        )

        rows = [
            (row.info.name, row.info.scope, row.active)
            for row in manager.installed_skills()
        ]
        assert rows == [
            ("bar", SkillScope.PROJECT, True),
            ("foo", SkillScope.GLOBAL, False),
        ]
        assert list(manager.available_skills) == ["bar"]
        assert [skill.name for skill in projection.catalogue] == ["bar"]
        assert projection.registry_versions == {"bar": ("cid", 5)}
        warned = [shadow.message % shadow.args for shadow in projection.shadowed]
        assert [message.split(" from ")[0] for message in warned] == ["Skill 'foo'"]
        assert warned[0].endswith("is shadowed by 'bar', which loads the same file")


class TestSkillManagerRediscovery:
    def test_edited_skill_is_picked_up_by_the_next_discovery(
        self, skills_dir: Path
    ) -> None:
        create_skill(skills_dir, "evolving", "Before")
        config = build_test_vibe_config(skill_paths=[skills_dir])
        assert SkillManager(lambda: config).available_skills[
            "evolving"
        ].description == ("Before")

        create_skill(skills_dir, "evolving", "After the edit")

        skill = SkillManager(lambda: config).available_skills["evolving"]
        assert skill.description == "After the edit"

    def test_unchanged_skill_is_not_parsed_again(
        self, skills_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vibe.core.skills import manager as manager_module

        create_skill(skills_dir, "stable")
        config = build_test_vibe_config(skill_paths=[skills_dir])
        SkillManager(lambda: config)
        parses: list[str] = []
        parse = manager_module.parse_skill_markdown

        def counting_parse(content: str) -> Any:
            parses.append(content)
            return parse(content)

        monkeypatch.setattr(manager_module, "parse_skill_markdown", counting_parse)

        assert "stable" in SkillManager(lambda: config).available_skills
        assert parses == []

    def test_openai_policy_added_later_is_picked_up(self, skills_dir: Path) -> None:
        skill_dir = create_skill(skills_dir, "late-policy")
        config = build_test_vibe_config(skill_paths=[skills_dir])
        assert (
            SkillManager(lambda: config).available_skills["late-policy"].model_invocable
        )

        (skill_dir / "agents").mkdir()
        (skill_dir / "agents" / "openai.yaml").write_text(
            "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
        )

        skill = SkillManager(lambda: config).available_skills["late-policy"]
        assert skill.model_invocable is False

    def test_cached_skill_still_reports_its_issues(self, skills_dir: Path) -> None:
        skill_dir = create_skill(skills_dir, "bad-policy")
        (skill_dir / "agents").mkdir()
        metadata_path = skill_dir / "agents" / "openai.yaml"
        metadata_path.write_text(
            'policy:\n  allow_implicit_invocation: "false"\n', encoding="utf-8"
        )
        config = build_test_vibe_config(skill_paths=[skills_dir])

        first = SkillManager(lambda: config)
        second = SkillManager(lambda: config)

        assert [issue.file for issue in first.config_issues] == [metadata_path]
        assert [issue.file for issue in second.config_issues] == [metadata_path]

    def test_cached_skill_still_warns_about_its_directory_name(
        self, skills_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vibe.core.skills import manager as manager_module

        create_skill(skills_dir, "original").rename(skills_dir / "renamed")
        config = build_test_vibe_config(skill_paths=[skills_dir])
        warnings: list[str] = []
        monkeypatch.setattr(
            manager_module.logger,
            "warning",
            lambda message, *args: warnings.append(message % args),
        )

        SkillManager(lambda: config)
        SkillManager(lambda: config)

        assert len([w for w in warnings if "doesn't match directory name" in w]) == 2
