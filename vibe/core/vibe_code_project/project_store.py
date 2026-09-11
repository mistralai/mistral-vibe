from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
import tomli_w

from vibe.core.paths import PROJECTS_FILE
from vibe.core.vibe_code_project.selection import (
    LocalProjectLink,
    ProjectLink,
    RemoteProjectLink,
    project_link_path,
)
from vibe.observability.logging import logger

REMOTE_PROJECT_KIND = "remote"
LOCAL_PROJECT_KIND = "local"


class _RemoteProjectEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["remote"]
    repo_root: str
    repo_url: str
    project_id: str
    project_name: str

    def to_link(self) -> RemoteProjectLink:
        return RemoteProjectLink(
            repo_root=Path(self.repo_root).expanduser().resolve(),
            repo_url=self.repo_url,
            project_id=self.project_id,
            project_name=self.project_name,
        )


class _LocalProjectEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["local"]
    directory_path: str
    project_id: str
    project_name: str

    def to_link(self) -> LocalProjectLink:
        return LocalProjectLink(
            directory_path=Path(self.directory_path).expanduser().resolve(),
            project_id=self.project_id,
            project_name=self.project_name,
        )


class VibeProjectsStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else PROJECTS_FILE.path

    def get_project_link(self, *, repo_root: Path) -> ProjectLink | None:
        normalized_root = _normalize_path(repo_root)
        for link in self.list_project_links():
            if _normalize_path(project_link_path(link)) == normalized_root:
                return link
        return None

    def upsert_project_link(self, link: ProjectLink) -> None:
        data = self._read_raw_document()
        entries = _raw_project_entries(data)
        normalized_root = _normalize_path(project_link_path(link))
        next_entries = [
            entry
            for entry in entries
            if not _entry_matches_key(entry, repo_root=normalized_root)
        ]
        next_entries.append(_project_link_to_entry(link))
        self._write_entries(data, next_entries)

    def delete_project_link(self, *, repo_root: Path) -> None:
        self._delete_project_link(repo_root=repo_root, remote_only=False)

    def list_project_links(self) -> list[ProjectLink]:
        links: list[ProjectLink] = []
        for entry in _raw_project_entries(self._read_raw_document()):
            if link := _parse_project_link(entry):
                links.append(link)
        return links

    def get_remote_project(self, *, repo_root: Path) -> RemoteProjectLink | None:
        link = self.get_project_link(repo_root=repo_root)
        return link if isinstance(link, RemoteProjectLink) else None

    def upsert_remote_project(self, link: RemoteProjectLink) -> None:
        self.upsert_project_link(link)

    def delete_remote_project(self, *, repo_root: Path) -> None:
        self._delete_project_link(repo_root=repo_root, remote_only=True)

    def _delete_project_link(self, *, repo_root: Path, remote_only: bool) -> None:
        data = self._read_raw_document()
        entries = _raw_project_entries(data)
        normalized_root = _normalize_path(repo_root)
        next_entries = [
            entry
            for entry in entries
            if not _entry_matches_key(
                entry, repo_root=normalized_root, remote_only=remote_only
            )
        ]
        self._write_entries(data, next_entries)

    def list_remote_projects(self) -> list[RemoteProjectLink]:
        return [
            link
            for link in self.list_project_links()
            if isinstance(link, RemoteProjectLink)
        ]

    def _read_raw_document(self) -> dict[str, object]:
        try:
            with self._path.open("rb") as file:
                data = tomllib.load(file)
        except FileNotFoundError:
            return {"version": 1, "projects": []}
        except (OSError, tomllib.TOMLDecodeError):
            logger.debug(
                "Failed to read Vibe projects file %s", self._path, exc_info=True
            )
            return {"version": 1, "projects": []}
        return dict(data)

    def _write_entries(
        self, data: dict[str, object], entries: Sequence[dict[str, object]]
    ) -> None:
        data["version"] = data.get("version", 1)
        data["projects"] = list(entries)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("wb") as file:
            tomli_w.dump(data, file)


def _normalize_path(path: Path) -> str:
    return str(path.expanduser().resolve())


def _raw_project_entries(data: dict[str, object]) -> list[dict[str, object]]:
    entries = data.get("projects")
    if not isinstance(entries, list):
        return []
    return [dict(entry) for entry in entries if isinstance(entry, dict)]


def _parse_project_link(entry: dict[str, object]) -> ProjectLink | None:
    try:
        if entry.get("kind") == LOCAL_PROJECT_KIND:
            return _LocalProjectEntry.model_validate(entry).to_link()
        return _RemoteProjectEntry.model_validate(entry).to_link()
    except ValidationError:
        return None


def _entry_matches_key(
    entry: dict[str, object], *, repo_root: str, remote_only: bool = False
) -> bool:
    link = _parse_project_link(entry)
    if link is None or (remote_only and not isinstance(link, RemoteProjectLink)):
        return False
    return _normalize_path(project_link_path(link)) == repo_root


def _link_to_entry(link: RemoteProjectLink) -> dict[str, object]:
    return {
        "kind": REMOTE_PROJECT_KIND,
        "repo_root": _normalize_path(link.repo_root),
        "repo_url": link.repo_url,
        "project_id": link.project_id,
        "project_name": link.project_name,
    }


def _project_link_to_entry(link: ProjectLink) -> dict[str, object]:
    if isinstance(link, RemoteProjectLink):
        return _link_to_entry(link)
    return {
        "kind": LOCAL_PROJECT_KIND,
        "directory_path": _normalize_path(link.directory_path),
        "project_id": link.project_id,
        "project_name": link.project_name,
    }
