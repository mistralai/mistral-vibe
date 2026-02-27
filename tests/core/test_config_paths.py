from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from vibe.core.paths.config_paths import (
    discover_local_agents_dirs,
    discover_local_skills_dirs,
    discover_local_tools_dirs,
)


class TestDiscoverLocalSkillsDirs:
    def test_returns_empty_list_when_dir_not_trusted(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = False
            assert discover_local_skills_dirs(tmp_path) == []

    def test_returns_empty_list_when_trusted_but_no_skills_dirs(
        self, tmp_path: Path
    ) -> None:
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            assert discover_local_skills_dirs(tmp_path) == []

    def test_returns_vibe_skills_only_when_only_it_exists(self, tmp_path: Path) -> None:
        vibe_skills = tmp_path / ".vibe" / "skills"
        vibe_skills.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_skills_dirs(tmp_path)
        assert result == [vibe_skills]

    def test_returns_agents_skills_only_when_only_it_exists(
        self, tmp_path: Path
    ) -> None:
        agents_skills = tmp_path / ".agents" / "skills"
        agents_skills.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_skills_dirs(tmp_path)
        assert result == [agents_skills]

    def test_returns_both_in_order_when_both_exist(self, tmp_path: Path) -> None:
        vibe_skills = tmp_path / ".vibe" / "skills"
        agents_skills = tmp_path / ".agents" / "skills"
        vibe_skills.mkdir(parents=True)
        agents_skills.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_skills_dirs(tmp_path)
        assert result == [vibe_skills, agents_skills]

    def test_ignores_vibe_skills_when_file_not_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "skills").write_text("", encoding="utf-8")
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_skills_dirs(tmp_path)
        assert result == []

    def test_finds_skills_dirs_recursively_in_trusted_folder(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / "sub" / ".agents" / "skills").mkdir(parents=True)
        (tmp_path / "sub" / "deep" / ".vibe" / "skills").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_skills_dirs(tmp_path)
        assert result == [
            tmp_path / ".vibe" / "skills",
            tmp_path / "sub" / ".agents" / "skills",
            tmp_path / "sub" / "deep" / ".vibe" / "skills",
        ]

    def test_does_not_descend_into_ignored_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / "node_modules" / ".vibe" / "skills").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_skills_dirs(tmp_path)
        assert result == [tmp_path / ".vibe" / "skills"]


class TestDiscoverLocalToolsDirs:
    def test_returns_empty_list_when_dir_not_trusted(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = False
            assert discover_local_tools_dirs(tmp_path) == []

    def test_returns_empty_list_when_trusted_but_no_tools_dir(
        self, tmp_path: Path
    ) -> None:
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            assert discover_local_tools_dirs(tmp_path) == []

    def test_returns_tools_dir_when_exists(self, tmp_path: Path) -> None:
        vibe_tools = tmp_path / ".vibe" / "tools"
        vibe_tools.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_tools_dirs(tmp_path)
        assert result == [vibe_tools]

    def test_ignores_tools_when_file_not_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "tools").write_text("", encoding="utf-8")
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_tools_dirs(tmp_path)
        assert result == []

    def test_finds_tools_dirs_recursively(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / "sub" / ".vibe" / "tools").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_tools_dirs(tmp_path)
        assert result == [
            tmp_path / ".vibe" / "tools",
            tmp_path / "sub" / ".vibe" / "tools",
        ]

    def test_does_not_descend_into_ignored_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".git" / ".vibe" / "tools").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_tools_dirs(tmp_path)
        assert result == [tmp_path / ".vibe" / "tools"]


class TestDiscoverLocalAgentsDirs:
    def test_returns_empty_list_when_dir_not_trusted(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = False
            assert discover_local_agents_dirs(tmp_path) == []

    def test_returns_empty_list_when_trusted_but_no_agents_dir(
        self, tmp_path: Path
    ) -> None:
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            assert discover_local_agents_dirs(tmp_path) == []

    def test_returns_agents_dir_when_exists(self, tmp_path: Path) -> None:
        vibe_agents = tmp_path / ".vibe" / "agents"
        vibe_agents.mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_agents_dirs(tmp_path)
        assert result == [vibe_agents]

    def test_ignores_agents_when_file_not_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "agents").write_text("", encoding="utf-8")
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_agents_dirs(tmp_path)
        assert result == []

    def test_finds_agents_dirs_recursively(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        (tmp_path / "sub" / "deep" / ".vibe" / "agents").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_agents_dirs(tmp_path)
        assert result == [
            tmp_path / ".vibe" / "agents",
            tmp_path / "sub" / "deep" / ".vibe" / "agents",
        ]

    def test_does_not_descend_into_ignored_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        (tmp_path / "__pycache__" / ".vibe" / "agents").mkdir(parents=True)
        with patch("vibe.core.paths.config_paths.trusted_folders_manager") as mock_tfm:
            mock_tfm.is_trusted.return_value = True
            result = discover_local_agents_dirs(tmp_path)
        assert result == [tmp_path / ".vibe" / "agents"]
