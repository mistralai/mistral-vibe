from __future__ import annotations

from pathlib import Path
import tomllib
from unittest.mock import patch

import pytest
import tomli_w

from vibe.core.paths import AGENTS_MD_FILENAME, TRUSTED_FOLDERS_FILE
from vibe.core.trusted_folders import (
    TrustedFoldersManager,
    find_git_repo_ancestor,
    find_repo_trustable_files_for_cwd,
    find_trustable_files,
    has_agents_md_file,
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


class TestIsTrustedInheritance:
    """Tests for the walk-up trust inheritance behaviour."""

    def test_child_of_trusted_folder_returns_true(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        assert manager.is_trusted(child) is True

    def test_child_of_untrusted_folder_returns_false(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_untrusted(tmp_path)
        child = tmp_path / "sub"
        child.mkdir()
        assert manager.is_trusted(child) is False

    def test_most_specific_ancestor_wins(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        manager = TrustedFoldersManager()
        manager.add_trusted(parent)
        manager.add_untrusted(child)

        assert manager.is_trusted(parent) is True
        assert manager.is_trusted(child) is False
        assert manager.is_trusted(child / "grandchild") is False

    def test_untrusted_parent_trusted_child(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)

        manager = TrustedFoldersManager()
        manager.add_untrusted(parent)
        manager.add_trusted(child)

        assert manager.is_trusted(parent) is False
        assert manager.is_trusted(child) is True
        assert manager.is_trusted(child / "grandchild") is True

    def test_deep_nesting_inherits_trust(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        assert manager.is_trusted(deep) is True

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        assert manager.is_trusted(tmp_path / "unknown") is None


class TestFindTrustRoot:
    def test_returns_path_when_path_is_trusted(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)
        assert manager.find_trust_root(tmp_path) == tmp_path.resolve()

    def test_returns_ancestor_when_child(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        assert manager.find_trust_root(child) == tmp_path.resolve()

    def test_returns_none_when_no_trusted_ancestor(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        assert manager.find_trust_root(tmp_path) is None

    def test_returns_closest_trusted_ancestor(self, tmp_path: Path) -> None:
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)
        manager.add_trusted(parent)
        # child should find parent (closest), not tmp_path
        assert manager.find_trust_root(child) == parent.resolve()

    def test_returns_none_when_closer_ancestor_is_untrusted(
        self, tmp_path: Path
    ) -> None:
        parent = tmp_path / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        manager = TrustedFoldersManager()
        manager.add_untrusted(parent)
        manager.add_trusted(tmp_path)
        # The closer untrusted ancestor blocks the higher trusted one.
        assert manager.find_trust_root(child) is None

    def test_returns_none_when_path_itself_is_untrusted(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_untrusted(tmp_path)
        assert manager.find_trust_root(tmp_path) is None


class TestIsExplicitlyUntrusted:
    def test_returns_true_for_path_in_untrusted_list(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_untrusted(tmp_path)
        assert manager.is_explicitly_untrusted(tmp_path) is True

    def test_returns_false_for_unknown_path(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        assert manager.is_explicitly_untrusted(tmp_path) is False

    def test_does_not_walk_ancestors(self, tmp_path: Path) -> None:
        child = tmp_path / "child"
        child.mkdir()
        manager = TrustedFoldersManager()
        manager.add_untrusted(tmp_path)
        # parent is untrusted, but child itself is not in the list
        assert manager.is_explicitly_untrusted(child) is False

    def test_returns_false_for_trusted_path(self, tmp_path: Path) -> None:
        manager = TrustedFoldersManager()
        manager.add_trusted(tmp_path)
        assert manager.is_explicitly_untrusted(tmp_path) is False


class TestHasAgentsMdFile:
    def test_returns_false_for_empty_directory(self, tmp_path: Path) -> None:
        assert has_agents_md_file(tmp_path) is False

    def test_returns_true_when_agents_md_exists(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agents", encoding="utf-8")
        assert has_agents_md_file(tmp_path) is True

    def test_returns_false_when_only_other_files_exist(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("", encoding="utf-8")
        (tmp_path / ".vibe").mkdir()
        assert has_agents_md_file(tmp_path) is False

    def test_agents_md_filename_constant(self) -> None:
        assert AGENTS_MD_FILENAME == "AGENTS.md"

    def test_unreadable_agents_md_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        denied = (tmp_path / "AGENTS.md").resolve()

        def fake_is_file(self: Path) -> bool:
            if self.resolve() == denied:
                raise PermissionError(13, "Permission denied")
            return False

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        assert has_agents_md_file(tmp_path) is False


class TestFindTrustableFiles:
    def test_returns_empty_for_clean_directory(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        assert find_trustable_files(tmp_path) == []

    def test_detects_vibe_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = find_trustable_files(tmp_path)
        assert ".vibe/" in result

    def test_detects_agents_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        result = find_trustable_files(tmp_path)
        assert ".agents/" in result

    def test_ignores_empty_vibe_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe").mkdir()
        assert find_trustable_files(tmp_path) == []

    def test_ignores_empty_agents_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".agents").mkdir()
        assert find_trustable_files(tmp_path) == []

    def test_detects_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agent", encoding="utf-8")
        result = find_trustable_files(tmp_path)
        assert "AGENTS.md" in result

    def test_returns_empty_when_no_trustable_content(self, tmp_path: Path) -> None:
        (tmp_path / "other.txt").write_text("", encoding="utf-8")
        assert find_trustable_files(tmp_path) == []

    def test_ignores_vibe_config_in_subfolder(self, tmp_path: Path) -> None:
        (tmp_path / "sub" / ".vibe" / "skills").mkdir(parents=True)
        assert find_trustable_files(tmp_path) == []

    def test_ignores_agents_skills_in_subfolder(self, tmp_path: Path) -> None:
        (tmp_path / "deep" / "nested" / ".agents" / "skills").mkdir(parents=True)
        assert find_trustable_files(tmp_path) == []

    def test_returns_empty_when_config_only_in_subfolder(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / ".vibe" / "skills").mkdir(parents=True)
        assert find_trustable_files(tmp_path) == []

    def test_detects_multiple_files_at_root(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / ".agents" / "skills").mkdir(parents=True)
        (tmp_path / "AGENTS.md").write_text("# Agent", encoding="utf-8")
        result = find_trustable_files(tmp_path)
        assert ".vibe/" in result
        assert ".agents/" in result
        assert "AGENTS.md" in result

    def test_no_duplicates_for_root_vibe_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        result = find_trustable_files(tmp_path)
        assert result.count(".vibe/") == 1


def _make_git_repo(path: Path) -> None:
    git_dir = path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


class TestFindRepoTrustableFilesForCwd:
    def test_returns_empty_when_repo_root_is_none(self, tmp_path: Path) -> None:
        assert find_repo_trustable_files_for_cwd(tmp_path, None) == []

    def test_returns_empty_when_repo_root_is_not_ancestor(self, tmp_path: Path) -> None:
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        assert find_repo_trustable_files_for_cwd(cwd, repo) == []

    def test_includes_root_trustable_files(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        (repo / ".vibe" / "skills").mkdir(parents=True)
        (repo / ".agents" / "skills").mkdir(parents=True)
        (repo / "AGENTS.md").write_text("# Agent", encoding="utf-8")
        cwd = repo / "src" / "pkg"
        cwd.mkdir(parents=True)

        assert find_repo_trustable_files_for_cwd(cwd, repo) == [
            ".agents/",
            ".vibe/",
            "AGENTS.md",
        ]

    def test_includes_agents_md_between_cwd_and_repo_root(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        cwd = repo / "src" / "pkg" / "deep"
        cwd.mkdir(parents=True)
        (repo / "src" / "AGENTS.md").write_text("# Source", encoding="utf-8")
        (repo / "src" / "pkg" / "AGENTS.md").write_text("# Package", encoding="utf-8")
        (cwd / "AGENTS.md").write_text("# Cwd", encoding="utf-8")

        assert find_repo_trustable_files_for_cwd(cwd, repo) == [
            "src/AGENTS.md",
            "src/pkg/AGENTS.md",
        ]


class TestFindGitRepoAncestor:
    def test_returns_path_when_directly_contains_git(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        assert find_git_repo_ancestor(tmp_path) == tmp_path.resolve()

    def test_ignores_git_file_pointer(self, tmp_path: Path) -> None:
        (tmp_path / ".git").write_text("gitdir: /elsewhere", encoding="utf-8")
        # Not a directory, so not treated as a repo root.
        assert find_git_repo_ancestor(tmp_path) is None

    def test_ignores_empty_git_directory(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        # Missing HEAD, so not a real repo.
        assert find_git_repo_ancestor(tmp_path) is None

    def test_returns_closest_ancestor_with_git(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert find_git_repo_ancestor(nested) == tmp_path.resolve()

    def test_returns_innermost_when_multiple_git_repos(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        inner = tmp_path / "sub"
        inner.mkdir()
        _make_git_repo(inner)
        child = inner / "deep"
        child.mkdir()
        assert find_git_repo_ancestor(child) == inner.resolve()

    def test_returns_none_when_no_git_anywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path.parent))
        assert find_git_repo_ancestor(tmp_path / "a") is None

    def test_excludes_home_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        _make_git_repo(home)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
        sub = home / "project"
        sub.mkdir()
        assert find_git_repo_ancestor(sub) is None

    def test_terminates_at_filesystem_root_without_git(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "nope"))
        assert find_git_repo_ancestor(tmp_path) is None

    def test_unreadable_ancestor_git_head_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ancestor = tmp_path / "mnt" / "vast"
        (ancestor / ".git").mkdir(parents=True)
        cwd = ancestor / "project"
        cwd.mkdir()

        real_is_file = Path.is_file
        denied = (ancestor / ".git" / "HEAD").resolve()

        def fake_is_file(self: Path) -> bool:
            if self.resolve() == denied:
                raise PermissionError(13, "Permission denied")
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))

        assert find_git_repo_ancestor(cwd) is None
