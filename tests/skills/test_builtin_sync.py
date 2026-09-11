from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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

    # Unskip with the Plugins section: it is withheld from this release, so the
    # skill documents no install directory and its description names no plugin.
    @pytest.mark.skip(reason="Plugins are withheld from the vibe skill")
    def test_vibe_skill_documents_plugin_install_directories(self) -> None:
        prompt = BUILTIN_SKILLS["vibe"].prompt

        assert "~/.vibe/plugins/<name>/" in prompt
        assert "<root>/.vibe/plugins/<name>/" in prompt
        assert "plugin.json" in prompt

    @pytest.mark.skip(reason="Plugins are withheld from the vibe skill")
    def test_vibe_skill_description_covers_plugin_requests(self) -> None:
        assert "plugin" in BUILTIN_SKILLS["vibe"].description

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

    def test_excluding_builtins_leaves_their_names_free(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.skills.manager.BUILTIN_SKILLS", BUILTIN_SKILLS)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        create_skill(skills_dir, "vibe", "Custom vibe override")

        config = build_test_vibe_config(skill_paths=[skills_dir])
        manager = SkillManager(lambda: config, include_builtins=False)

        # The name is reserved only because a builtin holds it. With none loaded
        # the user's skill is the only claimant, so it wins rather than vanishing.
        assert manager.available_skills["vibe"].description == "Custom vibe override"
        assert "skill-creator" not in manager.available_skills


class TestBuiltinSkillsShippedAsPluginSkills:
    """The `vibe` plugin carries a copy of each Python builtin.

    The copies are what a unified session loads; the Python originals still
    serve the legacy Host. Until the originals go, the pair has to be kept from
    drifting on the two fields that decide behaviour — the name the skill is
    reached by and the description the model routes on.
    """

    def _plugin_skill(self, name: str) -> dict[str, object]:
        import vibe.plugins.builtins as builtins_pkg

        path = (
            Path(builtins_pkg.__file__).parent / "vibe" / "skills" / name / "SKILL.md"
        )
        text = path.read_text(encoding="utf-8")
        _, frontmatter, body = text.split("---\n", 2)
        return {**yaml.safe_load(frontmatter), "body": body.strip()}

    @pytest.mark.parametrize("name", sorted(BUILTIN_SKILLS))
    def test_every_builtin_has_a_plugin_copy(self, name: str) -> None:
        assert self._plugin_skill(name)["name"] == name

    @pytest.mark.parametrize("name", sorted(BUILTIN_SKILLS))
    def test_the_copy_routes_on_the_same_description(self, name: str) -> None:
        assert (
            self._plugin_skill(name)["description"] == BUILTIN_SKILLS[name].description
        )

    @pytest.mark.parametrize("name", sorted(BUILTIN_SKILLS))
    def test_the_copy_keeps_the_invocability_of_the_original(self, name: str) -> None:
        copy = self._plugin_skill(name)
        assert copy.get("user-invocable", True) == BUILTIN_SKILLS[name].user_invocable

    def test_the_vibe_copy_does_not_pin_a_version_it_cannot_know(self) -> None:
        # A checked-in file cannot interpolate the running version the way
        # vibe.py does, so the copy points at main rather than at whatever
        # version happened to be current when it was generated.
        body = str(self._plugin_skill("vibe")["body"])
        assert "__VIBE_VERSION__" not in body
        assert "https://github.com/mistralai/mistral-vibe/blob/main/README.md" in body
