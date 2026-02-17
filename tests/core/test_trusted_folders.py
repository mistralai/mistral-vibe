from __future__ import annotations

from pathlib import Path
import tomllib
from unittest.mock import patch

import pytest
import tomli_w

from vibe.core.paths.global_paths import TRUSTED_FOLDERS_FILE
from vibe.core.trusted_folders import (
    AGENTS_MD_FILENAMES,
    TrustedFoldersManager,
    has_agents_md_file,
    has_trustable_content,
)


class TestTrustedFoldersManager:
    def test_initializes_with_empty_lists_when_file_does_not_exist(
        self, tmp_path: Path
    ) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path
        assert not trusted_file.is_file()

        manager = TrustedFoldersManager()
        assert manager.is_trusted(tmp_path) is None
        assert trusted_file.is_file()

    def test_loads_existing_file(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path

        data = {"trusted": [str(tmp_path.resolve())], "untrusted": []}
        with trusted_file.open("wb") as f:
            tomli_w.dump(data, f)

        manager = TrustedFoldersManager()

        assert manager.is_trusted(tmp_path) is True

    def test_handles_corrupted_file(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path
        trusted_file.write_text("invalid toml content {[", encoding="utf-8")

        manager = TrustedFoldersManager()

        assert manager.is_trusted(tmp_path) is None
        assert trusted_file.is_file()

    def test_normalizes_paths_to_absolute(
        self, tmp_working_directory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = TrustedFoldersManager()

        manager.add_trusted(Path("."))
        assert manager.is_trusted(tmp_working_directory) is True
        assert manager.is_trusted(Path(".")) is True

    def test_expands_user_home_in_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        manager = TrustedFoldersManager()

        manager.add_trusted(Path("~/test"))
        assert manager.is_trusted(tmp_path / "test") is True

    def test_is_trusted_returns_true_for_trusted_path(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)

        assert manager.is_trusted(tmp_path) is True

    def test_is_trusted_returns_false_for_untrusted_path(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_untrusted(tmp_path)

        assert manager.is_trusted(tmp_path) is False

    def test_is_trusted_returns_none_for_unknown_path(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()

        assert manager.is_trusted(tmp_path) is None

    def test_add_trusted_adds_path_to_trusted_list(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)

        assert manager.is_trusted(tmp_path) is True
        with trusted_file.open("rb") as f:
            data = tomllib.load(f)
        assert str(tmp_path.resolve()) in data["trusted"]

    def test_add_trusted_removes_path_from_untrusted(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path
        manager = TrustedFoldersManager()

        manager.add_untrusted(tmp_path)
        assert manager.is_trusted(tmp_path) is False

        manager.add_trusted(tmp_path)
        assert manager.is_trusted(tmp_path) is True

        with trusted_file.open("rb") as f:
            data = tomllib.load(f)
        assert str(tmp_path.resolve()) not in data["untrusted"]
        assert str(tmp_path.resolve()) in data["trusted"]

    def test_add_trusted_idempotent(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path

        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)
        manager.add_trusted(tmp_path)
        manager.add_trusted(tmp_path)

        assert manager.is_trusted(tmp_path) is True
        with trusted_file.open("rb") as f:
            data = tomllib.load(f)
        assert data["trusted"].count(str(tmp_path.resolve())) == 1

    def test_add_untrusted_adds_path_to_untrusted_list(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path
        manager = TrustedFoldersManager()
        manager.add_untrusted(tmp_path)

        assert manager.is_trusted(tmp_path) is False
        with trusted_file.open("rb") as f:
            data = tomllib.load(f)
        assert str(tmp_path.resolve()) in data["untrusted"]

    def test_add_untrusted_removes_path_from_trusted(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path
        manager = TrustedFoldersManager()

        manager.add_trusted(tmp_path)
        assert manager.is_trusted(tmp_path) is True

        manager.add_untrusted(tmp_path)
        assert manager.is_trusted(tmp_path) is False

        with trusted_file.open("rb") as f:
            data = tomllib.load(f)
        assert str(tmp_path.resolve()) not in data["trusted"]
        assert str(tmp_path.resolve()) in data["untrusted"]

    def test_add_untrusted_idempotent(self, tmp_path: Path) -> None:
        trusted_file = TRUSTED_FOLDERS_FILE.path

        manager = TrustedFoldersManager()
        manager.add_untrusted(tmp_path)
        manager.add_untrusted(tmp_path)
        manager.add_untrusted(tmp_path)

        assert manager.is_trusted(tmp_path) is False
        with trusted_file.open("rb") as f:
            data = tomllib.load(f)
        assert data["untrusted"].count(str(tmp_path.resolve())) == 1

    def test_persistence_across_instances(self, tmp_path: Path) -> None:
        manager1 = TrustedFoldersManager()
        manager1.add_trusted(tmp_path)

        manager2 = TrustedFoldersManager()
        assert manager2.is_trusted(tmp_path) is True

    def test_handles_multiple_paths(self, tmp_path: Path) -> None:
        trusted1 = tmp_path / "trusted1"
        trusted2 = tmp_path / "trusted2"
        untrusted1 = tmp_path / "untrusted1"
        untrusted2 = tmp_path / "untrusted2"
        for p in [trusted1, trusted2, untrusted1, untrusted2]:
            p.mkdir()

        manager = TrustedFoldersManager()
        manager.add_trusted(trusted1)
        manager.add_trusted(trusted2)
        manager.add_untrusted(untrusted1)
        manager.add_untrusted(untrusted2)

        assert manager.is_trusted(trusted1) is True
        assert manager.is_trusted(trusted2) is True
        assert manager.is_trusted(untrusted1) is False
        assert manager.is_trusted(untrusted2) is False

    def test_handles_switching_between_trusted_and_untrusted(
        self, tmp_path: Path
    ) -> None:
        manager = TrustedFoldersManager()

        manager.add_trusted(tmp_path)
        assert manager.is_trusted(tmp_path) is True

        manager.add_untrusted(tmp_path)
        assert manager.is_trusted(tmp_path) is False

        manager.add_trusted(tmp_path)
        assert manager.is_trusted(tmp_path) is True

    def test_handles_missing_file_during_save(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()

        def mock_open(*args, **kwargs):
            raise OSError("Permission denied")

        with patch("pathlib.Path.open", side_effect=mock_open):
            manager.add_trusted(tmp_path)

        assert manager.is_trusted(tmp_path) is True


class TestHasAgentsMdFile:
    def test_returns_false_for_empty_directory(self, tmp_path: Path) -> None:
        assert has_agents_md_file(tmp_path) is False

    def test_returns_true_when_agents_md_exists(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agents", encoding="utf-8")
        assert has_agents_md_file(tmp_path) is True

    def test_returns_true_when_vibe_md_exists(self, tmp_path: Path) -> None:
        (tmp_path / "VIBE.md").write_text("# Vibe", encoding="utf-8")
        assert has_agents_md_file(tmp_path) is True

    def test_returns_true_when_dot_vibe_md_exists(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe.md").write_text("# Vibe", encoding="utf-8")
        assert has_agents_md_file(tmp_path) is True

    def test_returns_false_when_only_other_files_exist(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("", encoding="utf-8")
        (tmp_path / ".vibe").mkdir()
        assert has_agents_md_file(tmp_path) is False

    def test_agents_md_filenames_constant(self) -> None:
        assert AGENTS_MD_FILENAMES == ["AGENTS.md", "VIBE.md", ".vibe.md"]


class TestHasTrustableContent:
    def test_returns_true_when_vibe_dir_exists(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        assert has_trustable_content(tmp_path) is True

    def test_returns_true_when_agents_dir_exists(self, tmp_path: Path) -> None:
        (tmp_path / ".agents").mkdir()
        assert has_trustable_content(tmp_path) is True

    def test_returns_true_when_agents_md_filename_exists(self, tmp_path: Path) -> None:
        for name in AGENTS_MD_FILENAMES:
            (tmp_path / name).write_text("", encoding="utf-8")
            assert has_trustable_content(tmp_path) is True
            (tmp_path / name).unlink()

    def test_returns_false_when_no_trustable_content(self, tmp_path: Path) -> None:
        (tmp_path / "other.txt").write_text("", encoding="utf-8")
        assert has_trustable_content(tmp_path) is False
