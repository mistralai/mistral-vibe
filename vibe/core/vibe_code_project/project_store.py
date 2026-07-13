from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
import tomli_w

from vibe.core.logger import logger
from vibe.core.paths import PROJECTS_FILE
from vibe.core.vibe_code_project.selection import VibeCodeProjectLink

REMOTE_PROJECT_KIND = "remote"


class _RemoteProjectEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["remote"]
    repo_root: str
    repo_url: str
    project_id: str
    project_name: str

    def to_link(self) -> VibeCodeProjectLink:
        return VibeCodeProjectLink(
            repo_root=Path(self.repo_root).expanduser().resolve(),
            repo_url=self.repo_url,
            project_id=self.project_id,
            project_name=self.project_name,
        )


class VibeProjectsStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else PROJECTS_FILE.path

    def get_remote_project(self, *, repo_root: Path) -> VibeCodeProjectLink | None:
        normalized_root = _normalize_path(repo_root)
        for link in self.list_remote_projects():
            if _normalize_path(link.repo_root) == normalized_root:
                return link
        return None

    def upsert_remote_project(self, link: VibeCodeProjectLink) -> None:
        data = self._read_raw_document()
        entries = _raw_project_entries(data)
        next_entries = [
            entry for entry in entries if not _entry_matches_link_key(entry, link)
        ]
        next_entries.append(_link_to_entry(link))
        self._write_entries(data, next_entries)

    def delete_remote_project(self, *, repo_root: Path) -> None:
        data = self._read_raw_document()
        entries = _raw_project_entries(data)
        normalized_root = _normalize_path(repo_root)
        next_entries = [
            entry
            for entry in entries
            if not _entry_matches_key(entry, repo_root=normalized_root)
        ]
        self._write_entries(data, next_entries)

    def list_remote_projects(self) -> list[VibeCodeProjectLink]:
        links: list[VibeCodeProjectLink] = []
        for entry in _raw_project_entries(self._read_raw_document()):
            if link := _parse_link(entry):
                links.append(link)
        return links

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


def _parse_link(entry: dict[str, object]) -> VibeCodeProjectLink | None:
    try:
        return _RemoteProjectEntry.model_validate(entry).to_link()
    except ValidationError:
        return None


def _entry_matches_link_key(
    entry: dict[str, object], link: VibeCodeProjectLink
) -> bool:
    return _entry_matches_key(entry, repo_root=_normalize_path(link.repo_root))


def _entry_matches_key(entry: dict[str, object], *, repo_root: str) -> bool:
    link = _parse_link(entry)
    if link is None:
        return False
    return _normalize_path(link.repo_root) == repo_root


def _link_to_entry(link: VibeCodeProjectLink) -> dict[str, object]:
    return {
        "kind": REMOTE_PROJECT_KIND,
        "repo_root": _normalize_path(link.repo_root),
        "repo_url": link.repo_url,
        "project_id": link.project_id,
        "project_name": link.project_name,
    }
