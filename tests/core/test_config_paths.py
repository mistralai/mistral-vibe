from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from vibe.core.paths.config_paths import resolve_local_skills_dirs


class TestResolveLocalSkillsDirs:
    def test_returns_empty_list_when_dir_not_trusted(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = False
            assert resolve_local_skills_dirs(tmp_path) == []

    def test_returns_empty_list_when_trusted_but_no_skills_dirs(
        self, tmp_path: Path
    ) -> None:
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            assert resolve_local_skills_dirs(tmp_path) == []

    def test_returns_vibe_skills_only_when_only_it_exists(self, tmp_path: Path) -> None:
        vibe_skills = tmp_path / ".vibe" / "skills"
        vibe_skills.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = resolve_local_skills_dirs(tmp_path)
        assert result == [vibe_skills]

    def test_returns_agents_skills_only_when_only_it_exists(
        self, tmp_path: Path
    ) -> None:
        agents_skills = tmp_path / ".agents" / "skills"
        agents_skills.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = resolve_local_skills_dirs(tmp_path)
        assert result == [agents_skills]

    def test_returns_both_in_order_when_both_exist(self, tmp_path: Path) -> None:
        vibe_skills = tmp_path / ".vibe" / "skills"
        agents_skills = tmp_path / ".agents" / "skills"
        vibe_skills.mkdir(parents=True)
        agents_skills.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = resolve_local_skills_dirs(tmp_path)
        assert result == [vibe_skills, agents_skills]

    def test_ignores_vibe_skills_when_file_not_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "skills").write_text("", encoding="utf-8")
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = resolve_local_skills_dirs(tmp_path)
        assert result == []
