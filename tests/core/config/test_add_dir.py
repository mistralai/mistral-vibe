from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.paths import AGENTS_MD_FILENAME
from vibe.core.tools.utils import is_path_within_workdir
from vibe.core.trusted_folders import trusted_folders_manager


class TestHarnessFilesManagerAdditionalDirs:
    def test_default_additional_dirs_empty(self) -> None:
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr._additional_dirs == ()

    def test_init_with_additional_dirs(self, tmp_path: Path) -> None:
        d1 = tmp_path / "extra1"
        d1.mkdir()
        d2 = tmp_path / "extra2"
        d2.mkdir()

        reset_harness_files_manager()
        init_harness_files_manager("user", "project", additional_dirs=[d1, d2])
        mgr = get_harness_files_manager()
        # init normalizes to resolved paths so the symlink/relative-vs-absolute
        # spellings of the same dir compare equal on reinit.
        assert mgr._additional_dirs == (d1.resolve(), d2.resolve())

    def test_reinit_idempotent_when_paths_resolve_equal(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target, target_is_directory=True)

        reset_harness_files_manager()
        init_harness_files_manager("user", "project", additional_dirs=[target])
        # Second call with the symlink spelling must NOT raise — both resolve
        # to the same dir.
        init_harness_files_manager("user", "project", additional_dirs=[link])

    def test_init_without_additional_dirs(self) -> None:
        reset_harness_files_manager()
        init_harness_files_manager("user", "project")
        mgr = get_harness_files_manager()
        assert mgr._additional_dirs == ()

    def test_reinit_with_same_additional_dirs_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        d = tmp_path / "extra"
        d.mkdir()

        reset_harness_files_manager()
        init_harness_files_manager("user", "project", additional_dirs=[d])
        # Same sources + same additional_dirs: no-op, no raise.
        init_harness_files_manager("user", "project", additional_dirs=[d])

    def test_reinit_with_different_additional_dirs_raises(self, tmp_path: Path) -> None:
        d1 = tmp_path / "extra1"
        d1.mkdir()
        d2 = tmp_path / "extra2"
        d2.mkdir()

        reset_harness_files_manager()
        init_harness_files_manager("user", "project", additional_dirs=[d1])
        with pytest.raises(RuntimeError, match="different configuration"):
            init_harness_files_manager("user", "project", additional_dirs=[d2])


class TestAdditionalDirsDiscovery:
    def test_discovers_tools_from_additional_dir(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra_project"
        tools_dir = extra / ".vibe" / "tools"
        tools_dir.mkdir(parents=True)

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        assert tools_dir in mgr.project_tools_dirs

    def test_discovers_skills_from_additional_dir(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra_project"
        skills_dir = extra / ".vibe" / "skills"
        skills_dir.mkdir(parents=True)

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        assert skills_dir in mgr.project_skills_dirs

    def test_discovers_agents_from_additional_dir(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra_project"
        agents_dir = extra / ".vibe" / "agents"
        agents_dir.mkdir(parents=True)

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        assert agents_dir in mgr.project_agents_dirs

    def test_discovers_agents_skills_from_additional_dir(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra_project"
        skills_dir = extra / ".agents" / "skills"
        skills_dir.mkdir(parents=True)

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        assert skills_dir in mgr.project_skills_dirs

    def test_discovers_prompts_from_additional_dir(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra_project"
        prompts_dir = extra / ".vibe" / "prompts"
        prompts_dir.mkdir(parents=True)

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        assert prompts_dir in mgr.project_prompts_dirs

    def test_no_dirs_when_additional_dir_has_no_vibe(self, tmp_path: Path) -> None:
        extra = tmp_path / "bare_project"
        extra.mkdir()

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        assert mgr.project_tools_dirs == []
        assert mgr.project_skills_dirs == []
        assert mgr.project_agents_dirs == []


class TestAdditionalDirsAgentsMd:
    def test_load_project_docs_includes_additional_dir_agents_md(
        self, tmp_path: Path
    ) -> None:
        extra = tmp_path / "extra_project"
        extra.mkdir()
        agents_md = extra / AGENTS_MD_FILENAME
        agents_md.write_text("Extra project instructions", encoding="utf-8")

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        docs = mgr.load_project_docs()
        assert any(d == extra and "Extra project" in content for d, content in docs)

    def test_load_project_docs_skips_missing_agents_md(self, tmp_path: Path) -> None:
        extra = tmp_path / "no_agents_md"
        extra.mkdir()

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        docs = mgr.load_project_docs()
        assert not any(d == extra for d, _ in docs)

    def test_load_project_docs_skips_empty_agents_md(self, tmp_path: Path) -> None:
        extra = tmp_path / "empty_agents_md"
        extra.mkdir()
        (extra / AGENTS_MD_FILENAME).write_text("   \n", encoding="utf-8")

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        docs = mgr.load_project_docs()
        assert not any(d == extra for d, _ in docs)

    def test_find_subdirectory_agents_md_in_additional_dir(
        self, tmp_path: Path
    ) -> None:
        extra = tmp_path / "extra_project"
        sub = extra / "sub"
        sub.mkdir(parents=True)
        agents_md = sub / AGENTS_MD_FILENAME
        agents_md.write_text("Sub instructions", encoding="utf-8")

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        file_in_sub = sub / "main.py"
        docs = mgr.find_subdirectory_agents_md(file_in_sub)
        assert any("Sub instructions" in content for _, content in docs)

    def test_find_subdirectory_agents_md_resolves_additional_dir(
        self, tmp_path: Path
    ) -> None:
        extra = tmp_path / "extra_project"
        sub = extra / "sub"
        sub.mkdir(parents=True)
        link = tmp_path / "extra_link"
        link.symlink_to(extra, target_is_directory=True)
        agents_md = sub / AGENTS_MD_FILENAME
        agents_md.write_text("Sub instructions", encoding="utf-8")

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(link,))
        docs = mgr.find_subdirectory_agents_md(sub / "main.py")
        assert any("Sub instructions" in content for _, content in docs)

    def test_find_subdirectory_agents_md_outside_all_dirs(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra"
        extra.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        docs = mgr.find_subdirectory_agents_md(outside / "file.py")
        assert docs == []


class TestFilePermissionsAdditionalDirs:
    def test_is_within_workdir_for_additional_dir(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra"
        extra.mkdir()
        file_in_extra = extra / "some_file.py"

        reset_harness_files_manager()
        init_harness_files_manager("user", "project", additional_dirs=[extra])

        assert is_path_within_workdir(str(file_in_extra))

    def test_is_within_workdir_resolves_additional_dir(self, tmp_path: Path) -> None:
        extra = tmp_path / "extra"
        extra.mkdir()
        link = tmp_path / "extra_link"
        link.symlink_to(extra, target_is_directory=True)
        file_in_extra = extra / "some_file.py"

        reset_harness_files_manager()
        init_harness_files_manager("user", "project", additional_dirs=[link])

        assert is_path_within_workdir(str(file_in_extra))

    def test_is_within_workdir_for_cwd(self, tmp_working_directory: Path) -> None:
        file_in_cwd = tmp_working_directory / "file.py"
        assert is_path_within_workdir(str(file_in_cwd))

    def test_is_not_within_workdir_for_outside_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)

        reset_harness_files_manager()
        init_harness_files_manager("user", "project")

        assert not is_path_within_workdir(str(outside / "file.py"))

    def test_is_path_within_workdir_when_manager_uninitialized(
        self, tmp_working_directory: Path
    ) -> None:
        reset_harness_files_manager()
        # Without a manager, only cwd counts.
        assert is_path_within_workdir(str(tmp_working_directory / "file.py"))


class TestAdditionalDirsAreImplicitlyTrusted:
    def test_load_project_docs_does_not_require_trust_for_additional_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Untrusted cwd: primary workdir contributes nothing.
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)

        extra = tmp_path / "extra_project"
        extra.mkdir()
        (extra / AGENTS_MD_FILENAME).write_text(
            "Trusted via --add-dir", encoding="utf-8"
        )

        mgr = HarnessFilesManager(
            sources=("user", "project"), _additional_dirs=(extra,)
        )
        docs = mgr.load_project_docs()
        assert any("Trusted via --add-dir" in content for _, content in docs)

    def test_project_tools_dirs_skip_trust_check_for_additional_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: False)

        extra = tmp_path / "extra_project"
        tools_dir = extra / ".vibe" / "tools"
        tools_dir.mkdir(parents=True)

        mgr = HarnessFilesManager(
            sources=("user", "project"), _additional_dirs=(extra,)
        )
        assert tools_dir in mgr.project_tools_dirs


class TestLoadProjectDocsDedupe:
    def test_dedupes_when_cwd_under_additional_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        extra = (tmp_path / "extra_project").resolve()
        extra.mkdir()
        sub = extra / "sub"
        sub.mkdir()
        (extra / AGENTS_MD_FILENAME).write_text("Root instructions", encoding="utf-8")
        (sub / AGENTS_MD_FILENAME).write_text("Sub instructions", encoding="utf-8")
        monkeypatch.chdir(sub)

        trusted_folders_manager.trust_for_session(extra)

        mgr = HarnessFilesManager(
            sources=("user", "project"), _additional_dirs=(extra,)
        )
        docs = mgr.load_project_docs()

        emitted_dirs = [d for d, _ in docs]
        assert emitted_dirs.count(extra) == 1
        assert sub.resolve() in emitted_dirs


class TestHookFilesAdditionalDirs:
    def test_hook_files_includes_additional_dir_hooks_toml(
        self, tmp_path: Path
    ) -> None:
        extra = tmp_path / "extra"
        extra.mkdir()
        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(extra,))
        assert extra / ".vibe" / "hooks.toml" in mgr.hook_files

    def test_hook_files_deduplicates_workdir_and_additional_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workdir = tmp_path / "project"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        trusted_folders_manager.trust_for_session(workdir)

        mgr = HarnessFilesManager(
            sources=("user", "project"), _additional_dirs=(workdir.resolve(),)
        )
        hook_file = workdir / ".vibe" / "hooks.toml"

        assert mgr.hook_files.count(hook_file.resolve()) == 1


class TestProjectPromptsDirsAdditionalDirs:
    def test_project_prompts_dirs_deduplicates_workdir_and_additional_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workdir = tmp_path / "project"
        (workdir / ".vibe" / "prompts").mkdir(parents=True)
        monkeypatch.chdir(workdir)
        trusted_folders_manager.trust_for_session(workdir)

        mgr = HarnessFilesManager(
            sources=("user", "project"), _additional_dirs=(workdir.resolve(),)
        )

        assert len(mgr.project_prompts_dirs) == 1


class TestProjectRootsNestedDedup:
    def test_nested_add_dirs_preserved(self, tmp_path: Path) -> None:
        outer = (tmp_path / "outer").resolve()
        inner = outer / "inner"
        inner.mkdir(parents=True)

        mgr = HarnessFilesManager(sources=("user",), _additional_dirs=(outer, inner))
        assert mgr.project_roots == [outer, inner]

    def test_add_dir_containing_cwd_keeps_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outer = (tmp_path / "outer").resolve()
        inner = outer / "inner"
        inner.mkdir(parents=True)
        (outer / AGENTS_MD_FILENAME).write_text("Outer instructions", encoding="utf-8")
        (inner / AGENTS_MD_FILENAME).write_text("Inner instructions", encoding="utf-8")
        monkeypatch.chdir(inner)
        trusted_folders_manager.trust_for_session(inner)
        trusted_folders_manager.trust_for_session(outer)

        mgr = HarnessFilesManager(
            sources=("user", "project"), _additional_dirs=(outer,)
        )
        # cwd survives so its walk-up semantics still work; the add-dir keeps
        # its own root-level discovery.
        assert mgr.project_roots == [inner, outer]
        docs = mgr.load_project_docs()
        assert any("Outer" in c for _, c in docs)
        assert any("Inner" in c for _, c in docs)

    def test_add_dir_nested_under_trusted_workdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        outer = (tmp_path / "outer").resolve()
        inner = outer / "inner"
        inner.mkdir(parents=True)
        skills_dir = inner / ".vibe" / "skills"
        skills_dir.mkdir(parents=True)
        monkeypatch.chdir(outer)
        trusted_folders_manager.trust_for_session(outer)

        mgr = HarnessFilesManager(
            sources=("user", "project"), _additional_dirs=(inner,)
        )
        assert mgr.project_roots == [outer, inner]
        assert skills_dir in mgr.project_skills_dirs
