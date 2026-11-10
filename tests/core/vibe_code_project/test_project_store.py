from __future__ import annotations

import os
from pathlib import Path
import stat
import tomllib

import pytest
import tomli_w

from vibe.core.vibe_code_project import (
    LocalProjectLink,
    RemoteProjectLink,
    VibeProjectsStore,
)


def _link(
    *,
    repo_root: Path,
    project_id: str = "project-1",
    repo_url: str = "https://github.com/mistralai/mistral-vibe.git",
) -> RemoteProjectLink:
    return RemoteProjectLink(
        repo_root=repo_root,
        repo_url=repo_url,
        project_id=project_id,
        project_name="Mistral Vibe",
    )


def _local_link(
    *, directory_path: Path, project_id: str = "project-1"
) -> LocalProjectLink:
    return LocalProjectLink(
        directory_path=directory_path,
        project_id=project_id,
        project_name="Mistral Vibe",
    )


def _read_toml(path: Path) -> dict:
    with path.open("rb") as file:
        return tomllib.load(file)


def test_projects_store_upserts_and_reads_remote_project(tmp_path: Path) -> None:
    path = tmp_path / "projects.toml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(path)

    store.upsert_project_link(_link(repo_root=repo_root))

    link = store.get_project_link(repo_root=repo_root)
    assert link == _link(repo_root=repo_root)
    assert _read_toml(path) == {
        "version": 1,
        "projects": [
            {
                "kind": "remote",
                "repo_root": str(repo_root.resolve()),
                "repo_url": "https://github.com/mistralai/mistral-vibe.git",
                "project_id": "project-1",
                "project_name": "Mistral Vibe",
            }
        ],
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_projects_store_creates_owner_only_registry(tmp_path: Path) -> None:
    """The project registry names every local project, so it lands owner-only."""
    path = tmp_path / "projects.toml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(path)

    store.upsert_project_link(_link(repo_root=repo_root))

    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_projects_store_keeps_existing_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "projects.toml"
    path.write_text("version = 1\nprojects = []\n", encoding="utf-8")
    path.chmod(0o644)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(path)

    store.upsert_project_link(_link(repo_root=repo_root))

    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_projects_store_upserts_and_reads_local_project(tmp_path: Path) -> None:
    path = tmp_path / "projects.toml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(path)

    store.upsert_project_link(_link(repo_root=repo_root, project_id="remote"))
    store.upsert_project_link(_local_link(directory_path=repo_root))

    assert store.get_project_link(repo_root=repo_root) == _local_link(
        directory_path=repo_root
    )
    assert _read_toml(path) == {
        "version": 1,
        "projects": [
            {
                "kind": "local",
                "directory_path": str(repo_root.resolve()),
                "project_id": "project-1",
                "project_name": "Mistral Vibe",
            }
        ],
    }


def test_projects_store_replaces_existing_remote_project_key(tmp_path: Path) -> None:
    path = tmp_path / "projects.toml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(path)

    store.upsert_project_link(_link(repo_root=repo_root, project_id="old"))
    store.upsert_project_link(_link(repo_root=repo_root, project_id="new"))

    assert [project["project_id"] for project in _read_toml(path)["projects"]] == [
        "new"
    ]
    assert store.get_project_link(repo_root=repo_root) == _link(
        repo_root=repo_root, project_id="new"
    )


def test_projects_store_deletes_project_link_without_touching_unknown_entries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "projects.toml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    with path.open("wb") as file:
        tomli_w.dump(
            {
                "version": 1,
                "projects": [
                    {
                        "kind": "local_desktop",
                        "repo_root": str(repo_root),
                        "project_id": "local",
                    }
                ],
            },
            file,
        )
    store = VibeProjectsStore(path)
    store.upsert_project_link(_link(repo_root=repo_root))

    store.delete_project_link(repo_root=repo_root)

    assert _read_toml(path) == {
        "version": 1,
        "projects": [
            {
                "kind": "local_desktop",
                "repo_root": str(repo_root),
                "project_id": "local",
            }
        ],
    }


def test_projects_store_ignores_corrupt_toml(tmp_path: Path) -> None:
    path = tmp_path / "projects.toml"
    path.write_text("{not-toml", encoding="utf-8")

    assert VibeProjectsStore(path).list_remote_projects() == []


def test_projects_store_reads_every_link_of_a_whole_file(tmp_path: Path) -> None:
    path = tmp_path / "projects.toml"
    store = VibeProjectsStore(path)
    link = _local_link(directory_path=tmp_path.resolve())

    assert store.read_project_links() == ([], True)
    store.upsert_project_link(link)
    assert store.read_project_links() == ([link], True)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{not-toml", id="corrupt"),
        pytest.param("", id="mid-write"),
        pytest.param(
            'version = 1\n[[projects]]\nkind = "future"\n', id="unknown-entry"
        ),
    ],
)
def test_projects_store_reports_a_partly_read_file(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "projects.toml"
    path.write_text(content, encoding="utf-8")

    assert VibeProjectsStore(path).read_project_links() == ([], False)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_projects_store_writes_through_a_symlinked_registry(tmp_path: Path) -> None:
    target = tmp_path / "dotfiles" / "projects.toml"
    target.parent.mkdir()
    target.write_text("version = 1\nprojects = []\n", encoding="utf-8")
    path = tmp_path / "projects.toml"
    path.symlink_to(target)
    link = _local_link(directory_path=tmp_path.resolve())

    VibeProjectsStore(path).upsert_project_link(link)

    assert path.is_symlink()
    assert VibeProjectsStore(target).list_project_links() == [link]
    assert list(target.parent.iterdir()) == [target]
