from __future__ import annotations

from pathlib import Path
import tomllib

import tomli_w

from vibe.core.vibe_code_project import VibeCodeProjectLink, VibeProjectsStore


def _link(
    *,
    repo_root: Path,
    project_id: str = "project-1",
    repo_url: str = "https://github.com/mistralai/mistral-vibe.git",
) -> VibeCodeProjectLink:
    return VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url=repo_url,
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

    store.upsert_remote_project(_link(repo_root=repo_root))

    link = store.get_remote_project(repo_root=repo_root)
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


def test_projects_store_replaces_existing_remote_project_key(tmp_path: Path) -> None:
    path = tmp_path / "projects.toml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(path)

    store.upsert_remote_project(_link(repo_root=repo_root, project_id="old"))
    store.upsert_remote_project(_link(repo_root=repo_root, project_id="new"))

    assert [project["project_id"] for project in _read_toml(path)["projects"]] == [
        "new"
    ]
    assert store.get_remote_project(repo_root=repo_root) == _link(
        repo_root=repo_root, project_id="new"
    )


def test_projects_store_deletes_remote_project_without_touching_other_kinds(
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
    store.upsert_remote_project(_link(repo_root=repo_root))

    store.delete_remote_project(repo_root=repo_root)

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
