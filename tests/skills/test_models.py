from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
import pytest

from vibe.core.skills.models import SkillInfo, SkillMetadata


class TestSkillMetadata:
    def test_creates_with_required_fields(self) -> None:
        meta = SkillMetadata(name="test-skill", description="A test skill")

        assert meta.name == "test-skill"
        assert meta.description == "A test skill"
        assert meta.license is None
        assert meta.compatibility is None
        assert meta.metadata == {}
        assert meta.allowed_tools == []
        assert meta.user_invocable is True

    def test_creates_with_all_fields(self) -> None:
        meta = SkillMetadata(
            name="full-skill",
            description="A skill with all fields",
            license="MIT",
            compatibility="Requires git",
            metadata={"author": "Test Author", "version": "1.0"},
            allowed_tools=["bash", "read_file"],
            user_invocable=False,
        )

        assert meta.name == "full-skill"
        assert meta.description == "A skill with all fields"
        assert meta.license == "MIT"
        assert meta.compatibility == "Requires git"
        assert meta.metadata == {"author": "Test Author", "version": "1.0"}
        assert meta.allowed_tools == ["bash", "read_file"]
        assert meta.user_invocable is False

    def test_raises_error_for_uppercase_name(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(name="Test-SKILL", description="A test skill")
        assert "name" in str(exc_info.value).lower()

    def test_raises_error_for_invalid_chars_in_name(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(name="test_skill@v1.0", description="A test skill")
        assert "name" in str(exc_info.value).lower()

    def test_raises_error_for_consecutive_hyphens(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(name="test--skill", description="A test skill")
        assert "name" in str(exc_info.value).lower()

    def test_raises_error_for_leading_trailing_hyphens(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(name="-test-skill-", description="A test skill")
        assert "name" in str(exc_info.value).lower()

    def test_parses_allowed_tools_from_space_delimited_string(self) -> None:
        meta = SkillMetadata(
            name="test",
            description="A test skill",
            allowed_tools="bash read_file grep",  # type: ignore[arg-type]
        )

        assert meta.allowed_tools == ["bash", "read_file", "grep"]

    def test_parses_allowed_tools_from_list(self) -> None:
        meta = SkillMetadata(
            name="test", description="A test skill", allowed_tools=["bash", "read_file"]
        )

        assert meta.allowed_tools == ["bash", "read_file"]

    def test_parses_allowed_tools_handles_none(self) -> None:
        meta = SkillMetadata(
            name="test",
            description="A test skill",
            allowed_tools=None,  # type: ignore[arg-type]
        )

        assert meta.allowed_tools == []

    def test_normalizes_metadata_values_to_strings(self) -> None:
        meta = SkillMetadata(
            name="test",
            description="A test skill",
            metadata={"version": 1.0, "count": 42},  # type: ignore[dict-item]
        )

        assert meta.metadata == {"version": "1.0", "count": "42"}

    def test_raises_error_for_missing_name(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(description="A test skill")  # type: ignore[call-arg]

        assert "name" in str(exc_info.value)

    def test_raises_error_for_missing_description(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(name="test")  # type: ignore[call-arg]

        assert "description" in str(exc_info.value)

    def test_raises_error_for_empty_name(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(name="", description="A test skill")

        assert "name" in str(exc_info.value).lower()

    def test_raises_error_for_empty_description(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            SkillMetadata(name="test", description="")

        assert "description" in str(exc_info.value).lower()


class TestSkillInfo:
    def test_creates_from_metadata(self, tmp_path: Path) -> None:
        skill_path = tmp_path / "test-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.touch()

        meta = SkillMetadata(
            name="test-skill", description="A test skill", license="MIT"
        )
        info = SkillInfo.from_metadata(meta, skill_path)

        assert info.name == "test-skill"
        assert info.description == "A test skill"
        assert info.license == "MIT"
        assert info.skill_path == skill_path.resolve()
        assert info.skill_dir == skill_path.parent.resolve()

    def test_creates_with_all_fields(self, tmp_path: Path) -> None:
        skill_path = tmp_path / "full-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.touch()

        info = SkillInfo(
            name="full-skill",
            description="A skill with all fields",
            license="Apache-2.0",
            compatibility="git, docker",
            metadata={"author": "Test"},
            allowed_tools=["bash"],
            user_invocable=False,
            skill_path=skill_path,
        )

        assert info.name == "full-skill"
        assert info.description == "A skill with all fields"
        assert info.license == "Apache-2.0"
        assert info.compatibility == "git, docker"
        assert info.metadata == {"author": "Test"}
        assert info.allowed_tools == ["bash"]
        assert info.user_invocable is False
        assert info.skill_path == skill_path
        assert info.skill_dir == skill_path.parent.resolve()

    def test_from_metadata_resolves_paths(self, tmp_path: Path) -> None:
        skill_path = tmp_path / "test-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.touch()

        meta = SkillMetadata(name="test-skill", description="A test skill")
        info = SkillInfo.from_metadata(meta, skill_path)

        assert info.skill_path.is_absolute()
        assert info.skill_dir.is_absolute()

    def test_inherits_all_metadata_fields(self, tmp_path: Path) -> None:
        skill_path = tmp_path / "test-skill" / "SKILL.md"
        skill_path.parent.mkdir()
        skill_path.touch()

        meta = SkillMetadata(
            name="test-skill",
            description="A test skill",
            license="MIT",
            compatibility="Requires Python 3.12",
            metadata={"key": "value"},
            allowed_tools=["bash", "grep"],
            user_invocable=False,
        )
        info = SkillInfo.from_metadata(meta, skill_path)

        assert info.license == meta.license
        assert info.compatibility == meta.compatibility
        assert info.metadata == meta.metadata
        assert info.allowed_tools == meta.allowed_tools
        assert info.user_invocable == meta.user_invocable
