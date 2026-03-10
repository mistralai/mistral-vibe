from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.trusted_folders import trusted_folders_manager


class TestTrustedWorkdir:
    def test_returns_none_when_project_not_in_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.trusted_workdir is None

    def test_returns_none_when_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.trusted_workdir is None

    def test_returns_cwd_when_project_in_sources_and_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.trusted_workdir == tmp_path


class TestProjectToolsDirs:
    def test_returns_empty_when_project_not_in_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.project_tools_dirs == []

    def test_returns_empty_when_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_tools_dirs == []

    def test_returns_empty_when_tools_dir_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_tools_dirs == []

    def test_returns_path_when_tools_dir_exists_and_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        tools_dir = tmp_path / ".vibe" / "tools"
        tools_dir.mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_tools_dirs == [tools_dir]

    def test_ignores_tools_when_file_not_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "tools").write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_tools_dirs == []

    def test_finds_tools_dirs_recursively(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / "sub" / ".vibe" / "tools").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_tools_dirs == [
            tmp_path / ".vibe" / "tools",
            tmp_path / "sub" / ".vibe" / "tools",
        ]

    def test_does_not_descend_into_ignored_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe" / "tools").mkdir(parents=True)
        (tmp_path / ".git" / ".vibe" / "tools").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_tools_dirs == [tmp_path / ".vibe" / "tools"]


class TestProjectAgentsDirs:
    def test_returns_empty_when_project_not_in_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.project_agents_dirs == []

    def test_returns_empty_when_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_agents_dirs == []

    def test_returns_empty_when_agents_dir_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_agents_dirs == []

    def test_returns_path_when_agents_dir_exists_and_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        agents_dir = tmp_path / ".vibe" / "agents"
        agents_dir.mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_agents_dirs == [agents_dir]

    def test_ignores_agents_when_file_not_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "agents").write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_agents_dirs == []

    def test_finds_agents_dirs_recursively(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        (tmp_path / "sub" / "deep" / ".vibe" / "agents").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_agents_dirs == [
            tmp_path / ".vibe" / "agents",
            tmp_path / "sub" / "deep" / ".vibe" / "agents",
        ]

    def test_does_not_descend_into_ignored_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe" / "agents").mkdir(parents=True)
        (tmp_path / "__pycache__" / ".vibe" / "agents").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_agents_dirs == [tmp_path / ".vibe" / "agents"]


class TestUserToolsDirs:
    def test_returns_empty_when_user_not_in_sources(self) -> None:
        mgr = HarnessFilesManager(sources=("project",))
        assert mgr.user_tools_dirs == []

    def test_returns_empty_when_dir_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.user_tools_dirs == []

    def test_returns_path_when_user_in_sources_and_dir_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.user_tools_dirs == [tools_dir]


class TestUserSkillsDirs:
    def test_returns_empty_when_user_not_in_sources(self) -> None:
        mgr = HarnessFilesManager(sources=("project",))
        assert mgr.user_skills_dirs == []

    def test_returns_empty_when_dir_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.user_skills_dirs == []

    def test_returns_path_when_user_in_sources_and_dir_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.user_skills_dirs == [skills_dir]


class TestUserAgentsDirs:
    def test_returns_empty_when_user_not_in_sources(self) -> None:
        mgr = HarnessFilesManager(sources=("project",))
        assert mgr.user_agents_dirs == []

    def test_returns_empty_when_dir_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.user_agents_dirs == []

    def test_returns_path_when_user_in_sources_and_dir_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.user_agents_dirs == [agents_dir]


class TestProjectSkillsDirs:
    def test_returns_empty_list_when_no_skills_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == []

    def test_returns_vibe_skills_only_when_only_it_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        vibe_skills = tmp_path / ".vibe" / "skills"
        vibe_skills.mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == [vibe_skills]

    def test_returns_agents_skills_only_when_only_it_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        agents_skills = tmp_path / ".agents" / "skills"
        agents_skills.mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == [agents_skills]

    def test_returns_both_in_order_when_both_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        vibe_skills = tmp_path / ".vibe" / "skills"
        agents_skills = tmp_path / ".agents" / "skills"
        vibe_skills.mkdir(parents=True)
        agents_skills.mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == [vibe_skills, agents_skills]

    def test_ignores_vibe_skills_when_file_not_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe").mkdir()
        (tmp_path / ".vibe" / "skills").write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == []

    def test_returns_empty_when_project_not_in_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        vibe_skills = tmp_path / ".vibe" / "skills"
        vibe_skills.mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.project_skills_dirs == []

    def test_returns_empty_when_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)
        vibe_skills = tmp_path / ".vibe" / "skills"
        vibe_skills.mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == []

    def test_finds_skills_dirs_recursively_in_trusted_folder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / "sub" / ".agents" / "skills").mkdir(parents=True)
        (tmp_path / "sub" / "deep" / ".vibe" / "skills").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == [
            tmp_path / ".vibe" / "skills",
            tmp_path / "sub" / ".agents" / "skills",
            tmp_path / "sub" / "deep" / ".vibe" / "skills",
        ]

    def test_does_not_descend_into_ignored_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / ".vibe" / "skills").mkdir(parents=True)
        (tmp_path / "node_modules" / ".vibe" / "skills").mkdir(parents=True)
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.project_skills_dirs == [tmp_path / ".vibe" / "skills"]
