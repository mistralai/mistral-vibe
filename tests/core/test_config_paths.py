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


class TestLoadProjectDocs:
    def test_returns_empty_when_project_not_in_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.load_project_docs() == []

    def test_returns_empty_when_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)
        (tmp_path / "AGENTS.md").write_text("# Hello", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.load_project_docs() == []

    def test_returns_single_doc_when_trust_root_is_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        monkeypatch.setattr(
            trusted_folders_manager, "find_trust_root", lambda _: tmp_path.resolve()
        )
        (tmp_path / "AGENTS.md").write_text("# Root doc", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        docs = mgr.load_project_docs()
        assert len(docs) == 1
        assert docs[0] == (tmp_path.resolve(), "# Root doc")

    def test_walks_up_to_trust_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        monkeypatch.chdir(child)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        monkeypatch.setattr(
            trusted_folders_manager, "find_trust_root", lambda _: tmp_path.resolve()
        )
        (tmp_path / "AGENTS.md").write_text("# Root", encoding="utf-8")
        (child / "AGENTS.md").write_text("# Child", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        docs = mgr.load_project_docs()
        # outermost first
        assert docs[0] == (tmp_path.resolve(), "# Root")
        assert docs[-1] == (child.resolve(), "# Child")

    def test_skips_dirs_without_agents_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        monkeypatch.chdir(child)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        monkeypatch.setattr(
            trusted_folders_manager, "find_trust_root", lambda _: tmp_path.resolve()
        )
        # Only root has AGENTS.md, intermediate "sub" does not
        (tmp_path / "AGENTS.md").write_text("# Root", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        docs = mgr.load_project_docs()
        assert len(docs) == 1
        assert docs[0] == (tmp_path.resolve(), "# Root")

    def test_stops_at_trust_root_boundary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trust_root = tmp_path / "root"
        child = trust_root / "sub"
        child.mkdir(parents=True)
        monkeypatch.chdir(child)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        monkeypatch.setattr(
            trusted_folders_manager, "find_trust_root", lambda _: trust_root.resolve()
        )
        # Place AGENTS.md above trust root — should NOT be loaded
        (tmp_path / "AGENTS.md").write_text("# Above root", encoding="utf-8")
        (trust_root / "AGENTS.md").write_text("# At root", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        docs = mgr.load_project_docs()
        assert len(docs) == 1
        assert docs[0][0] == trust_root.resolve()

    def test_returns_empty_when_trust_root_not_ancestor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        child = tmp_path / "project" / "sub"
        child.mkdir(parents=True)
        trust_root = tmp_path / "other-root"
        trust_root.mkdir()
        monkeypatch.chdir(child)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        monkeypatch.setattr(
            trusted_folders_manager, "find_trust_root", lambda _: trust_root.resolve()
        )
        (tmp_path / "AGENTS.md").write_text("# Outside", encoding="utf-8")
        (child / "AGENTS.md").write_text("# Child", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.load_project_docs() == []

    def test_ignores_empty_agents_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        monkeypatch.setattr(
            trusted_folders_manager, "find_trust_root", lambda _: tmp_path.resolve()
        )
        (tmp_path / "AGENTS.md").write_text("   \n  ", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.load_project_docs() == []


class TestFindSubdirectoryAgentsMd:
    def test_returns_empty_when_project_not_in_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("# Sub", encoding="utf-8")
        target = sub / "file.py"
        target.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.find_subdirectory_agents_md(target) == []

    def test_returns_empty_when_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "find_trust_root", lambda _: None)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("# Sub", encoding="utf-8")
        target = sub / "file.py"
        target.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.find_subdirectory_agents_md(target) == []

    def test_returns_empty_when_file_outside_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cwd = tmp_path / "project"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        outside = tmp_path / "other" / "file.py"
        outside.parent.mkdir(parents=True)
        outside.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.find_subdirectory_agents_md(outside) == []

    def test_returns_empty_when_file_directly_in_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        (tmp_path / "AGENTS.md").write_text("# Root", encoding="utf-8")
        target = tmp_path / "file.py"
        target.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        # cwd-level AGENTS.md is handled by load_project_docs, not this method
        assert mgr.find_subdirectory_agents_md(target) == []

    def test_returns_agents_md_from_file_parent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("# Sub instructions", encoding="utf-8")
        target = sub / "file.py"
        target.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        docs = mgr.find_subdirectory_agents_md(target)
        assert len(docs) == 1
        assert docs[0] == (sub.resolve(), "# Sub instructions")

    def test_returns_multiple_agents_md_outermost_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        outer = tmp_path / "a"
        inner = outer / "b"
        inner.mkdir(parents=True)
        (outer / "AGENTS.md").write_text("# Outer", encoding="utf-8")
        (inner / "AGENTS.md").write_text("# Inner", encoding="utf-8")
        target = inner / "file.py"
        target.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        docs = mgr.find_subdirectory_agents_md(target)
        assert len(docs) == 2
        assert docs[0] == (outer.resolve(), "# Outer")
        assert docs[1] == (inner.resolve(), "# Inner")

    def test_skips_dirs_without_agents_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        outer = tmp_path / "a"
        inner = outer / "b"
        inner.mkdir(parents=True)
        # Only inner has AGENTS.md
        (inner / "AGENTS.md").write_text("# Inner", encoding="utf-8")
        target = inner / "file.py"
        target.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        docs = mgr.find_subdirectory_agents_md(target)
        assert len(docs) == 1
        assert docs[0] == (inner.resolve(), "# Inner")

    def test_ignores_empty_agents_md(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "AGENTS.md").write_text("   \n  ", encoding="utf-8")
        target = sub / "file.py"
        target.write_text("", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user", "project"))
        assert mgr.find_subdirectory_agents_md(target) == []


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


class TestLoadUserDoc:
    def test_returns_empty_when_user_not_in_sources(self) -> None:
        mgr = HarnessFilesManager(sources=("project",))
        assert mgr.load_user_doc() == ""

    def test_returns_empty_when_file_does_not_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.load_user_doc() == ""

    def test_returns_file_content_when_file_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        (tmp_path / "AGENTS.md").write_text("# User instructions", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.load_user_doc() == "# User instructions"

    def test_returns_empty_string_for_whitespace_only_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # load_user_doc strips — consistent with _collect_agents_md
        monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_path)
        (tmp_path / "AGENTS.md").write_text("   \n  ", encoding="utf-8")
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.load_user_doc() == ""
