from __future__ import annotations

from pathlib import Path

from vibe.app_server._local_projects import LocalProject
from vibe.app_server._session_project_match import ProjectFolders
from vibe.core.paths import WORKTREES_DIR


def _project(project_id: str, *roots: Path, created_at: int = 1) -> LocalProject:
    return LocalProject(
        id=project_id,
        name=project_id,
        root_paths=roots,
        created_at=created_at,
        updated_at=created_at,
    )


def _matched_id(projects: list[LocalProject], cwd: Path | None) -> str | None:
    project = ProjectFolders(projects).match(None if cwd is None else str(cwd))
    return project.id if project is not None else None


def test_the_folder_and_its_subfolders_match(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    projects = [_project("repo", repo)]

    assert _matched_id(projects, repo) == "repo"
    assert _matched_id(projects, repo / "sub") == "repo"
    assert _matched_id(projects, repo / "sub" / "deeper") == "repo"
    assert _matched_id(projects, tmp_path) is None
    assert _matched_id(projects, None) is None


def test_a_sibling_sharing_the_prefix_does_not_match(tmp_path: Path) -> None:
    projects = [_project("foo", tmp_path / "foo")]

    assert _matched_id(projects, tmp_path / "foobar") is None
    assert _matched_id(projects, tmp_path / "foobar" / "sub") is None


def test_the_deepest_project_wins(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    projects = [_project("repo", repo, created_at=0), _project("app", repo / "app")]

    assert _matched_id(projects, repo / "app" / "src") == "app"
    assert _matched_id(projects, repo / "app") == "app"
    assert _matched_id(projects, repo / "lib") == "repo"


def test_a_worktree_matches_only_its_exact_folder() -> None:
    worktree = WORKTREES_DIR.path / "bucket" / "name"
    projects = [_project("home", WORKTREES_DIR.path.parent.parent)]

    assert _matched_id(projects, worktree) is None
    assert _matched_id([_project("tree", worktree)], worktree) == "tree"
    assert _matched_id([_project("tree", worktree)], worktree / "sub") is None


def test_the_exact_tie_break_holds_for_a_subfolder(tmp_path: Path) -> None:
    repo, other = tmp_path / "repo", tmp_path / "other"
    projects = [
        _project("multi", repo, other, created_at=0),
        _project("single", repo, created_at=5),
    ]

    assert _matched_id(projects, repo / "sub") == "single"


def test_a_symlinked_folder_matches_its_target(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    link = tmp_path / "link"
    link.symlink_to(repo)

    assert _matched_id([_project("repo", repo)], link) == "repo"


def test_a_single_folder_project_wins_a_tie(tmp_path: Path) -> None:
    repo, other = tmp_path / "repo", tmp_path / "other"
    projects = [
        _project("multi", repo, other, created_at=0),
        _project("single", repo, created_at=5),
    ]

    assert _matched_id(projects, repo) == "single"


def test_then_the_project_whose_primary_folder_it_is(tmp_path: Path) -> None:
    repo, other = tmp_path / "repo", tmp_path / "other"
    projects = [
        _project("secondary", other, repo, created_at=0),
        _project("primary", repo, other, created_at=5),
    ]

    assert _matched_id(projects, repo) == "primary"


def test_then_the_oldest_then_the_lowest_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    assert (
        _matched_id(
            [_project("b", repo, created_at=1), _project("c", repo, created_at=2)], repo
        )
        == "b"
    )
    assert (
        _matched_id(
            [_project("b", repo, created_at=1), _project("a", repo, created_at=1)], repo
        )
        == "a"
    )
