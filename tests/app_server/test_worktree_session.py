from __future__ import annotations

from pathlib import Path

from git import Repo

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._worktree_session import session_harness_files
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.git.worktree import WorktreeRepository
from vibe.core.tools.manager import ToolManager


def _init_repo(root: Path) -> None:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    repo.close()


def test_access_roots_are_writable_but_never_opened_as_projects(tmp_path: Path) -> None:
    cwd, other = tmp_path / "cwd", tmp_path / "other"
    cwd.mkdir()
    other.mkdir()

    files = session_harness_files(
        HarnessFilesManager(sources=("user", "project")),
        cwd,
        [],
        [other, cwd, cwd / ".." / "other"],
    )

    assert files.access_roots == (other.resolve(),)
    assert other.resolve() not in files.project_roots
    assert other.resolve() not in files.writable_roots


def test_a_worktree_session_leaves_out_the_checkout_it_was_made_from(
    tmp_path: Path,
) -> None:
    repo, other = tmp_path / "repo", tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    (repo / "sub").mkdir()
    _init_repo(repo)
    with WorktreeRepository.open(repo) as repository:
        worktree = repository.prepare_auto(suggested_name="feature")

    files = session_harness_files(
        HarnessFilesManager(sources=("user",)),
        worktree.root,
        [],
        [repo, repo / "sub", other],
    )

    assert files.access_roots == (other.resolve(),)


def test_entering_a_worktree_drops_the_access_roots_in_its_checkout(
    tmp_path: Path,
) -> None:
    repo, other = (tmp_path / "repo").resolve(), (tmp_path / "other").resolve()
    (repo / "frontend").mkdir(parents=True)
    (repo / "backend").mkdir()
    other.mkdir()
    _init_repo(repo)
    with WorktreeRepository.open(repo) as repository:
        worktree = repository.prepare_auto(suggested_name="feature").root.resolve()
    files = session_harness_files(
        HarnessFilesManager(sources=("user",)),
        repo / "frontend",
        [],
        [repo / "backend", other],
    )
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config())
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=repo / "frontend",
        harness_files=files,
    )

    manager.move_to(worktree, [worktree])
    tool = manager.get("write_file")

    assert tool.harness_files.access_roots == (other,)
    assert not tool.workspace.allows(repo / "backend" / "a.txt")
    assert tool.workspace.allows(other / "a.txt")


def _monorepo_worktree(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo, other = (tmp_path / "repo").resolve(), (tmp_path / "other").resolve()
    for folder in ("frontend", "backend"):
        (repo / folder).mkdir(parents=True)
        (repo / folder / "file.txt").write_text(f"{folder}\n")
    other.mkdir()
    _init_repo(repo)
    git = Repo(repo)
    git.index.add(["frontend/file.txt", "backend/file.txt"])
    git.index.commit("folders")
    git.close()
    with WorktreeRepository.open(repo) as repository:
        worktree = repository.prepare_auto(suggested_name="feature").root.resolve()
    return repo, worktree, other


def test_a_worktree_session_writes_the_worktrees_copy_of_a_folder_in_its_checkout(
    tmp_path: Path,
) -> None:
    repo, worktree, other = _monorepo_worktree(tmp_path)

    files = session_harness_files(
        HarnessFilesManager(sources=("user",)),
        worktree / "frontend",
        [],
        [repo / "backend", other],
    )

    assert files.access_roots == (worktree / "backend", other)


def test_leaving_a_worktree_takes_its_folders_back_to_the_checkout(
    tmp_path: Path,
) -> None:
    repo, worktree, other = _monorepo_worktree(tmp_path)

    files = session_harness_files(
        HarnessFilesManager(sources=("user",)),
        repo / "frontend",
        [],
        [worktree / "backend", other],
    )

    assert files.access_roots == (repo / "backend", other)


def test_entering_a_worktree_moves_a_folder_of_its_checkout_into_it(
    tmp_path: Path,
) -> None:
    repo, worktree, other = _monorepo_worktree(tmp_path)
    files = session_harness_files(
        HarnessFilesManager(sources=("user",)),
        repo / "frontend",
        [],
        [repo / "backend", other],
    )
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config())
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=repo / "frontend",
        harness_files=files,
    )

    manager.move_to(worktree / "frontend", [worktree / "frontend"])
    tool = manager.get("write_file")

    assert tool.workspace.allows(worktree / "backend" / "a.txt")
    assert not tool.workspace.allows(repo / "backend" / "a.txt")
