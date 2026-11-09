from __future__ import annotations

import os
from pathlib import Path
import sys

import git as git_module
import pytest

import vibe.core.git.repo as repo_module
from vibe.utils.platform import (
    configure_git_python_executable,
    is_windows,
    resolve_git_executable,
)


def _git_name() -> str:
    return "git.exe" if is_windows() else "git"


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def _clear_git_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_PYTHON_GIT_EXECUTABLE", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def test_automatic_resolution_skips_project_and_relative_path_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    trusted_bin = tmp_path / "trusted-bin"
    project.mkdir()
    _make_executable(project / _git_name())
    trusted_git = _make_executable(trusted_bin / _git_name())
    monkeypatch.setenv("PATH", os.pathsep.join((".", str(project), str(trusted_bin))))

    assert resolve_git_executable(cwd=project) == str(trusted_git.resolve())


def test_automatic_resolution_fails_soft_when_only_project_git_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _make_executable(project / _git_name())
    monkeypatch.setenv("PATH", str(project))

    assert resolve_git_executable(cwd=project) is None


def test_absolute_override_allows_explicit_portable_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    portable_git = _make_executable(project / "tools" / _git_name())
    monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", str(portable_git))

    assert resolve_git_executable(cwd=project) == str(portable_git.resolve())


def test_relative_path_override_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", f"tools{os.sep}{_git_name()}")
    monkeypatch.setenv("PATH", "")

    assert resolve_git_executable(cwd=project) is None


def test_bare_name_override_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    trusted_git = _make_executable(tmp_path / "trusted-bin" / _git_name())
    project.mkdir()
    monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", _git_name())
    monkeypatch.setenv("PATH", str(trusted_git.parent))

    assert resolve_git_executable(cwd=project) is None


def test_gitpython_is_pinned_to_resolved_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    trusted_git = _make_executable(tmp_path / "trusted-bin" / _git_name())
    project.mkdir()
    monkeypatch.setenv("PATH", str(trusted_git.parent))

    assert configure_git_python_executable(cwd=project) == str(trusted_git.resolve())
    assert os.environ["GIT_PYTHON_GIT_EXECUTABLE"] == str(trusted_git.resolve())


def test_gitpython_cache_is_refreshed_after_early_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    trusted_git = _make_executable(tmp_path / "trusted-bin" / _git_name())
    project.mkdir()
    expected = str(trusted_git.resolve())
    refreshed: list[str] = []
    monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", expected)
    monkeypatch.setattr(git_module.Git, "GIT_PYTHON_GIT_EXECUTABLE", "git")

    def record_refresh(path: str) -> bool:
        refreshed.append(path)
        return True

    monkeypatch.setattr(git_module, "refresh", record_refresh)

    repo_module._git_python()

    assert refreshed == [expected]


def test_standard_per_user_windows_install_works_when_cwd_is_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "AppData" / "Local"
    git = _make_executable(local_app_data / "Programs" / "Git" / "cmd" / "git.exe")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("PATHEXT", ".EXE")
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    assert resolve_git_executable(cwd=tmp_path) == str(git.resolve())


def test_system_git_is_trusted_when_cwd_is_filesystem_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted_git = _make_executable(tmp_path / "bin" / _git_name())
    monkeypatch.setenv("PATH", str(trusted_git.parent))

    assert resolve_git_executable(cwd=Path(tmp_path.anchor)) == str(
        trusted_git.resolve()
    )


def test_user_local_git_is_trusted_when_cwd_is_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    trusted_git = _make_executable(home / ".local" / "bin" / _git_name())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("PATH", str(trusted_git.parent))

    assert resolve_git_executable(cwd=home) == str(trusted_git.resolve())


def test_git_directly_in_home_is_still_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _make_executable(home / _git_name())
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("PATH", str(home))

    assert resolve_git_executable(cwd=home) is None


def test_unreadable_path_entry_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    trusted_git = _make_executable(tmp_path / "trusted-bin" / _git_name())
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join((str(tmp_path / "disconnected"), str(trusted_git.parent))),
    )
    original_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        if "disconnected" in self.parts:
            raise OSError(5, "Access is denied")
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)

    assert resolve_git_executable(cwd=project) == str(trusted_git.resolve())
