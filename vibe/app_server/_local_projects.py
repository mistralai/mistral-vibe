"""Local projects, stored in ``~/.vibe/local_projects.toml``; the app server is its only writer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import tomllib
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import tomli_w

from vibe.app_server._time import now_ms
from vibe.app_server.protocol import LocalProjectCloudState
from vibe.core.paths import LOCAL_PROJECTS_FILE
from vibe.core.vibe_code_project import ProjectLink, project_link_path
from vibe.utils.file_lock import exclusive_file_lock

__all__ = [
    "CloudBacking",
    "LocalProject",
    "LocalProjectError",
    "LocalProjectNotFound",
    "LocalProjectStore",
    "LocalProjectStoreUnreadable",
]

_VERSION = 1


class LocalProjectError(Exception):
    """A request the store refuses, such as an empty name."""


class LocalProjectNotFound(LocalProjectError):
    pass


class LocalProjectStoreUnreadable(Exception):
    """The file exists but cannot be parsed, so it is never overwritten."""


@dataclass(frozen=True, slots=True)
class CloudBacking:
    project_id: str
    state: LocalProjectCloudState


@dataclass(frozen=True, slots=True)
class LocalProject:
    id: str
    name: str
    # The first root is the session working folder.
    root_paths: tuple[Path, ...]
    created_at: int
    updated_at: int
    cloud: CloudBacking | None = None


class _CloudEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: str
    state: LocalProjectCloudState


class _ProjectEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    name: str
    root_paths: list[str] = Field(min_length=1)
    created_at: int
    updated_at: int
    cloud: _CloudEntry | None = None

    def to_project(self) -> LocalProject:
        return LocalProject(
            id=self.id,
            name=self.name,
            root_paths=tuple(Path(root) for root in self.root_paths),
            created_at=self.created_at,
            updated_at=self.updated_at,
            cloud=(
                CloudBacking(project_id=self.cloud.project_id, state=self.cloud.state)
                if self.cloud is not None
                else None
            ),
        )


class LocalProjectStore:
    """Mutations rewrite raw entries, so fields a newer server wrote survive them."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else LOCAL_PROJECTS_FILE.path

    def list_projects(self) -> list[LocalProject]:
        return [
            project
            for entry in _entries(self._read())
            if (project := _parse(entry)) is not None
        ]

    def create(self, *, name: str, root_path: Path) -> LocalProject:
        entry = _new_entry(_clean_name(name), root_path, now_ms())
        with exclusive_file_lock(self._path):
            data = self._read()
            self._write(data, [*_entries(data), entry])
        return _ProjectEntry.model_validate(entry).to_project()

    def rename(self, project_id: str, name: str) -> LocalProject:
        clean = _clean_name(name)
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            entry = _find(entries, project_id)
            entry["name"] = clean
            entry["updated_at"] = now_ms()
            self._write(data, entries)
        return _ProjectEntry.model_validate(entry).to_project()

    def remove(self, project_id: str) -> None:
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            target = _find(entries, project_id)
            self._write(data, [entry for entry in entries if entry is not target])

    def set_cloud(self, project_id: str, cloud: CloudBacking) -> LocalProject:
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            entry = _find(entries, project_id)
            _set_cloud(entry, cloud, now_ms())
            self._write(data, entries)
        return _ProjectEntry.model_validate(entry).to_project()

    def import_links(self, links: Sequence[ProjectLink]) -> None:
        """Gives each linked folder a local project backed by the link's cloud project."""
        # A first pass without the lock keeps the usual nothing-to-import case read-only.
        data = self._read()
        if not _is_supported(data) or not _import_links(_entries(data), links):
            return
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            if _is_supported(data) and _import_links(entries, links):
                self._write(data, entries)

    def _read(self) -> dict[str, object]:
        try:
            with self._path.open("rb") as file:
                return tomllib.load(file)
        except FileNotFoundError:
            return {"version": _VERSION, "projects": []}
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise LocalProjectStoreUnreadable(
                f"Cannot read the local projects file {self._path}: {exc}"
            ) from exc

    def _write(self, data: dict[str, object], entries: list[dict[str, object]]) -> None:
        if not _is_supported(data):
            raise LocalProjectStoreUnreadable(
                f"Unsupported local projects file {self._path}, left untouched"
            )
        data["version"] = _VERSION
        data["projects"] = entries
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp creates the file owner-only: it names every local project.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                tomli_w.dump(data, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _is_supported(data: dict[str, object]) -> bool:
    projects = data.get("projects", [])
    return (
        data.get("version", _VERSION) == _VERSION
        and isinstance(projects, list)
        and all(isinstance(entry, dict) for entry in projects)
    )


def _new_entry(name: str, root_path: Path, now: int) -> dict[str, object]:
    return {
        "id": f"lp_{uuid4().hex}",
        "name": name,
        "root_paths": [str(root_path)],
        "created_at": now,
        "updated_at": now,
    }


def _set_cloud(entry: dict[str, object], cloud: CloudBacking, now: int) -> None:
    entry["cloud"] = {"project_id": cloud.project_id, "state": cloud.state}
    entry["updated_at"] = now


def _import_links(
    entries: list[dict[str, object]], links: Sequence[ProjectLink]
) -> bool:
    """Applies the missing links to ``entries`` in place; returns whether anything changed."""
    # Read raw so an entry this server can't parse still counts as imported.
    linked: set[tuple[Path, str]] = set()
    backed_roots: set[Path] = set()
    unbacked: dict[Path, dict[str, object]] = {}
    for entry in entries:
        roots = _raw_roots(entry)
        cloud = entry.get("cloud")
        cloud_id = cloud.get("project_id") if isinstance(cloud, dict) else None
        if isinstance(cloud_id, str):
            linked.update((root, cloud_id) for root in roots)
        if not roots:
            continue
        if isinstance(cloud, dict) and cloud.get("state") != "retired":
            backed_roots.add(roots[0])
        elif _parse(entry) is not None:
            unbacked.setdefault(roots[0], entry)
    now = now_ms()
    changed = False
    for link in links:
        root = project_link_path(link)
        key = (root, link.project_id)
        # A folder whose project has a live backing is imported, even if relinked since.
        if (
            not link.project_id.strip()
            or key in linked
            or root in backed_roots
            or root.parent == root
            # Unlike Path.is_dir, also False when the folder can't be accessed.
            or not os.path.isdir(root)
        ):
            continue
        entry = unbacked.pop(root, None)
        if entry is None:
            entry = _new_entry(link.project_name.strip() or root.name, root, now)
            entries.append(entry)
        _set_cloud(entry, CloudBacking(project_id=link.project_id, state="backed"), now)
        linked.add(key)
        backed_roots.add(root)
        changed = True
    return changed


def _raw_roots(entry: dict[str, object]) -> list[Path]:
    roots = entry.get("root_paths")
    if not isinstance(roots, list):
        return []
    return [Path(root) for root in roots if isinstance(root, str)]


def _entries(data: dict[str, object]) -> list[dict[str, object]]:
    entries = data.get("projects")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _parse(entry: dict[str, object]) -> LocalProject | None:
    try:
        return _ProjectEntry.model_validate(entry).to_project()
    except ValidationError:
        return None


def _find(entries: list[dict[str, object]], project_id: str) -> dict[str, object]:
    for entry in entries:
        if entry.get("id") == project_id and _parse(entry) is not None:
            return entry
    raise LocalProjectNotFound(f"No local project {project_id!r}")


def _clean_name(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise LocalProjectError("A project name cannot be empty")
    return clean
