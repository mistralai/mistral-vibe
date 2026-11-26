"""Local projects, stored in ``~/.vibe/local_projects.toml``; the app server is its only writer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
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
from vibe.core.types import SessionProject
from vibe.core.vibe_code_project import (
    ProjectLink,
    VibeProjectsStore,
    project_link_path,
)
from vibe.utils.file_lock import exclusive_file_lock

__all__ = [
    "CloudBacking",
    "CloudOwner",
    "LocalProject",
    "LocalProjectConflict",
    "LocalProjectError",
    "LocalProjectNotFound",
    "LocalProjectStore",
    "LocalProjectStoreUnreadable",
    "session_project",
]

_VERSION = 1


class LocalProjectError(Exception):
    """A request the store refuses, such as an empty name."""


class LocalProjectNotFound(LocalProjectError):
    pass


class LocalProjectConflict(LocalProjectError):
    """The folder's project is already backed by another cloud project."""


class LocalProjectStoreUnreadable(Exception):
    """The file exists but cannot be parsed, so it is never overwritten."""


@dataclass(frozen=True, slots=True)
class CloudOwner:
    organization_id: str
    workspace_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class CloudBacking:
    project_id: str
    state: LocalProjectCloudState
    # None for a backing made before owners were kept, or imported from a folder link.
    owner: CloudOwner | None = None


@dataclass(frozen=True, slots=True)
class LocalProject:
    id: str
    name: str
    # The first root is the session working folder; the others are context folders.
    root_paths: tuple[Path, ...]
    created_at: int
    updated_at: int
    cloud: CloudBacking | None = None
    # The projects merged into this one: their sessions follow it.
    merged_ids: tuple[str, ...] = ()


def session_project(
    projects: Sequence[LocalProject], project_id: str
) -> SessionProject:
    """Mirrors the runner's ``sessionProject``: a backed project goes by its cloud id, and still by its own."""
    for project in projects:
        cloud = project.cloud
        known_as = (
            cloud.project_id
            if cloud is not None and cloud.state == "backed"
            else project.id
        )
        if project_id in {known_as, project.id}:
            return SessionProject(kind="local", id=project.id)
    return SessionProject(kind="cloud", id=project_id)


class _OwnerEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    organization_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class _CloudEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: str
    state: LocalProjectCloudState
    owner: _OwnerEntry | None = None

    def to_backing(self) -> CloudBacking:
        return CloudBacking(
            project_id=self.project_id,
            state=self.state,
            owner=CloudOwner(**self.owner.model_dump()) if self.owner else None,
        )


class _ProjectEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    name: str
    root_paths: list[str] = Field(min_length=1)
    created_at: int
    updated_at: int
    cloud: _CloudEntry | None = None
    merged_ids: list[str] = []

    def to_project(self) -> LocalProject:
        return LocalProject(
            id=self.id,
            name=self.name,
            root_paths=tuple(Path(root) for root in self.root_paths),
            created_at=self.created_at,
            updated_at=self.updated_at,
            cloud=self.cloud.to_backing() if self.cloud is not None else None,
            merged_ids=tuple(self.merged_ids),
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

    def project_ids(self) -> set[str]:
        # Raw, so an entry this server can't parse still counts as existing.
        return {
            project_id
            for entry in _entries(self._read())
            if isinstance(project_id := entry.get("id"), str)
            and "deleted_at" not in entry
        }

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

    def set_roots(
        self, project_id: str, root_paths: Sequence[Path], links: VibeProjectsStore
    ) -> LocalProject:
        """Replaces the project's folders; the first becomes its working folder."""
        roots = list(dict.fromkeys(root_paths))
        if not roots:
            raise LocalProjectError("A project needs at least one folder")
        with exclusive_file_lock(self._path):
            data = self._read()
            self._check_supported(data)
            entries = _entries(data)
            entry = _find(entries, project_id)
            current = _raw_roots(entry)
            for root in roots:
                # Unlike a folder it has, a new one or a new working folder must be there.
                is_new = root not in current or (root == roots[0] != current[0])
                if is_new and not os.path.isdir(root):
                    raise LocalProjectError(f"Not a folder: {str(root)!r}")
            # A retired backing still names its cloud project: relinking it backs it again.
            cloud = entry.get("cloud")
            cloud_id = cloud.get("project_id") if isinstance(cloud, dict) else None
            project_links, _ = links.read_project_links()
            linked = {
                project_link_path(link): link.project_id
                for link in project_links
                if link.project_id.strip()
            }
            for root in roots:
                # The link import would move it out to its own cloud project.
                if root not in current and linked.get(root, cloud_id) != cloud_id:
                    raise LocalProjectConflict(
                        f"{root} is linked to another cloud project"
                    )
            # Unlinked, or the link import would add them back.
            unlinked = [
                root
                for root in current
                if root not in roots
                and cloud_id is not None
                and linked.get(root) == cloud_id
            ]
            # The link import drops a backing none of whose folders is linked.
            if (
                unlinked
                and _backing_id(entry) is not None
                and not any(linked.get(root) == cloud_id for root in roots)
            ):
                raise LocalProjectError(
                    f"{unlinked[0]} links the project to its cloud project"
                )
            for root in unlinked:
                links.delete_project_link(repo_root=root)
            entry["root_paths"] = [str(root) for root in roots]
            entry["updated_at"] = now_ms()
            self._write(data, entries)
        return _ProjectEntry.model_validate(entry).to_project()

    def remove(self, project_id: str, links: VibeProjectsStore) -> None:
        """Drops the project and the folder links that would import it back; its folders, sessions and worktrees stay."""
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            self._drop_with_links(data, entries, [_find(entries, project_id)], links)

    def purge_removed(self, links: VibeProjectsStore) -> None:
        """Drops the projects an older server only hid, with the folder links that would import them back."""
        # A first pass without the lock keeps the usual nothing-to-purge case read-only.
        data = self._read()
        if not _is_supported(data) or not _hidden(_raw_entries(data)):
            return
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _raw_entries(data)
            hidden = _hidden(entries)
            if _is_supported(data) and hidden:
                self._drop_with_links(data, entries, hidden, links)

    def set_cloud(self, project_id: str, cloud: CloudBacking) -> LocalProject:
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            entry = _find(entries, project_id)
            if cloud.state == "backed" and any(
                other is not entry and _backing_id(other) == cloud.project_id
                for other in entries
            ):
                raise LocalProjectError(
                    f"Cloud project {cloud.project_id!r} already backs a local project"
                )
            _set_cloud(entry, _next_backing(entry, cloud), now_ms())
            self._write(data, entries)
        return _ProjectEntry.model_validate(entry).to_project()

    def link_cloud(
        self,
        *,
        root_path: Path,
        cloud_project_id: str,
        name: str,
        owner: CloudOwner | None,
    ) -> LocalProject:
        """Adds the folder to the cloud project's local project, created from the folder's if none."""
        clean = _clean_name(name)
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            own: dict[str, object] | None = None
            holder: LocalProject | None = None
            for entry in entries:
                project = _parse(entry)
                if project is None or root_path not in project.root_paths:
                    continue
                active = _active_backing(project)
                if active == cloud_project_id:
                    return project
                # Refused rather than moved: the folder's sessions would follow it to the new cloud project.
                if active is not None:
                    raise LocalProjectConflict(
                        f"{root_path} is already linked to another cloud project"
                    )
                if project.root_paths[0] != root_path:
                    holder = holder or project
                elif own is None:
                    own = entry
            # Backed later, its project would drop a folder another cloud project links.
            if own is None and holder is not None:
                raise LocalProjectConflict(
                    f"{root_path} is a folder of the local project {holder.name!r}"
                )
            now = now_ms()
            backing = next(
                (entry for entry in entries if _backing_id(entry) == cloud_project_id),
                None,
            )
            if backing is not None:
                # One local project per cloud project: the folder joins it as a context folder.
                if own is None:
                    _add_roots(backing, [root_path])
                else:
                    _absorb(backing, own)
                    _drop(entries, [own])
                backing["updated_at"] = now
                entry = backing
            else:
                if own is None:
                    entry = _new_entry(clean, root_path, now)
                    entries.append(entry)
                else:
                    entry = own
                    entry["name"] = clean
                _set_cloud(
                    entry,
                    CloudBacking(
                        project_id=cloud_project_id, state="backed", owner=owner
                    ),
                    now,
                )
            self._write(data, entries)
        return _ProjectEntry.model_validate(entry).to_project()

    def retire_cloud(self, cloud_project_id: str) -> None:
        """Retires the backing by a cloud project that is gone; the local project stays."""
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _entries(data)
            entry = next(
                (entry for entry in entries if _backing_id(entry) == cloud_project_id),
                None,
            )
            if entry is None:
                return
            project = _parse(entry)
            retired = CloudBacking(
                project_id=cloud_project_id,
                state="retired",
                owner=project.cloud.owner if project and project.cloud else None,
            )
            _set_cloud(entry, retired, now_ms())
            self._write(data, entries)

    def import_links(self, links: VibeProjectsStore) -> None:
        """Gives each linked folder's cloud project a local project backed by it."""
        # A first pass without the lock keeps the usual nothing-to-import case read-only.
        data = self._read()
        if not _is_supported(data) or not _import_links(
            _raw_entries(data), *links.read_project_links()
        ):
            return
        with exclusive_file_lock(self._path):
            data = self._read()
            entries = _raw_entries(data)
            # Read again under the lock: a removal holding it may have just dropped some.
            project_links, complete = links.read_project_links()
            if _is_supported(data) and _import_links(entries, project_links, complete):
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

    def _drop_with_links(
        self,
        data: dict[str, object],
        entries: list[dict[str, object]],
        dropped: list[dict[str, object]],
        links: VibeProjectsStore,
    ) -> None:
        self._check_supported(data)
        # Links first: one left behind would import the project back.
        dropped_ids = {id(entry) for entry in dropped}
        kept = [entry for entry in entries if id(entry) not in dropped_ids]
        # A kept project may share a link with a dropped one.
        stale = _link_keys(dropped) - _link_keys(kept)
        # A link this server can't read stays and may import the project back later; refusing would block the removal.
        project_links, _ = links.read_project_links()
        for link in project_links:
            if (project_link_path(link), link.project_id) in stale:
                links.delete_project_link(repo_root=project_link_path(link))
        _drop(entries, dropped)
        self._write(data, entries)

    def _check_supported(self, data: dict[str, object]) -> None:
        if not _is_supported(data):
            raise LocalProjectStoreUnreadable(
                f"Unsupported local projects file {self._path}, left untouched"
            )

    def _write(self, data: dict[str, object], entries: list[dict[str, object]]) -> None:
        self._check_supported(data)
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
        # A plain UUID: the code-server contract validates project ids as UUIDs.
        "id": str(uuid4()),
        "name": name,
        "root_paths": [str(root_path)],
        "created_at": now,
        "updated_at": now,
    }


def _next_backing(entry: dict[str, object], cloud: CloudBacking) -> CloudBacking:
    """``cloud``, unless it would take a backed project from its cloud project or its account."""
    project = _parse(entry)
    current = project.cloud if project is not None else None
    if current is None or current.state != "backed" or cloud.state != "backed":
        return cloud
    if current.project_id != cloud.project_id:
        raise LocalProjectError(
            f"Local project is already backed by cloud project {current.project_id!r}"
        )
    if current.owner is None:
        return cloud
    if cloud.owner is not None and cloud.owner != current.owner:
        raise LocalProjectError(
            f"Cloud project {cloud.project_id!r} backs this project for another account"
        )
    return CloudBacking(
        project_id=cloud.project_id, state=cloud.state, owner=current.owner
    )


def _active_backing(project: LocalProject) -> str | None:
    cloud = project.cloud
    return cloud.project_id if cloud is not None and cloud.state != "retired" else None


def _set_cloud(entry: dict[str, object], cloud: CloudBacking, now: int) -> None:
    stored: dict[str, object] = {"project_id": cloud.project_id, "state": cloud.state}
    if cloud.owner is not None:
        stored["owner"] = asdict(cloud.owner)
    entry["cloud"] = stored
    entry["updated_at"] = now


def _import_links(
    entries: list[dict[str, object]], links: Sequence[ProjectLink], complete: bool
) -> bool:
    """Applies the links to ``entries`` in place; returns whether anything changed."""
    now = now_ms()
    changed = _merge_backings(entries)
    # Read before following links: a backing dropped in this pass still names its cloud project.
    names = _cloud_names(entries)
    # Only the whole set of links can tell that a folder was unlinked.
    if complete and _follow_links(entries, links, names, now):
        changed = True
    # Read raw so an entry this server can't parse still counts as imported.
    linked: set[tuple[Path, str]] = set()
    # A cloud project some backing retired is gone: no other folder's link backs a project with it.
    retired_ids: set[str] = set()
    backed_roots: set[Path] = set()
    unbacked: dict[Path, dict[str, object]] = {}
    for entry in entries:
        roots = _raw_roots(entry)
        cloud = entry.get("cloud")
        cloud_id = cloud.get("project_id") if isinstance(cloud, dict) else None
        if isinstance(cloud_id, str):
            linked.update((root, cloud_id) for root in roots)
            if isinstance(cloud, dict) and cloud.get("state") == "retired":
                retired_ids.add(cloud_id)
        if not roots:
            continue
        if isinstance(cloud, dict) and cloud.get("state") != "retired":
            backed_roots.add(roots[0])
        elif _parse(entry) is not None:
            unbacked.setdefault(roots[0], entry)
    backings = {
        cloud_id: entry
        for entry in entries
        if (cloud_id := _backing_id(entry)) is not None
    }
    for link in [link for link in links if link.project_id not in retired_ids]:
        root = project_link_path(link)
        key = (root, link.project_id)
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
        backing = backings.get(link.project_id)
        if backing is not None:
            # One local project per cloud project: the folder joins it as a context folder.
            if entry is None:
                _add_roots(backing, [root])
            else:
                _absorb(backing, entry)
                _drop(entries, [entry])
            backing["updated_at"] = now
        else:
            name = names.setdefault(link.project_id, _link_name(link, root))
            if entry is None:
                entry = _new_entry(name, root, now)
                entries.append(entry)
            else:
                entry["name"] = name
            _set_cloud(
                entry, CloudBacking(project_id=link.project_id, state="backed"), now
            )
            backings[link.project_id] = entry
            backed_roots.add(root)
        linked.add(key)
        changed = True
    return changed


def _follow_links(
    entries: list[dict[str, object]],
    links: Sequence[ProjectLink],
    names: dict[str, str],
    now: int,
) -> bool:
    """Points each backing at its folder's current link, or drops it once unlinked."""
    links_by_root = {
        project_link_path(link): link for link in links if link.project_id.strip()
    }
    # Backings that still follow a link, found first so a moved backing can join one.
    backings: dict[str, dict[str, object]] = {}
    stale: list[tuple[dict[str, object], Path]] = []
    for entry in entries:
        project = _parse(entry)
        if project is None or project.cloud is None or project.cloud.state != "backed":
            continue
        linked_ids = {
            link.project_id
            for root in project.root_paths
            if (link := links_by_root.get(root)) is not None
        }
        if project.cloud.project_id in linked_ids:
            backings[project.cloud.project_id] = entry
        else:
            stale.append((entry, project.root_paths[0]))
    merged: list[dict[str, object]] = []
    for entry, root in stale:
        link = links_by_root.get(root)
        if link is None:
            # The local project stays, under its local id.
            del entry["cloud"]
            entry["updated_at"] = now
            continue
        backing = backings.get(link.project_id)
        if backing is not None:
            _absorb(backing, entry)
            backing["updated_at"] = now
            merged.append(entry)
            continue
        # The old name belongs to the old cloud project.
        entry["name"] = names.setdefault(link.project_id, _link_name(link, root))
        _set_cloud(entry, CloudBacking(project_id=link.project_id, state="backed"), now)
        backings[link.project_id] = entry
    _drop(entries, merged)
    # A context folder linked to another cloud project leaves, for the import to place it.
    moved = False
    for entry in entries:
        cloud = entry.get("cloud")
        if not isinstance(cloud, dict) or cloud.get("state") not in {
            "backed",
            "retired",
        }:
            continue
        cloud_id = cloud.get("project_id")
        roots = _raw_roots(entry)
        kept = roots[:1] + [
            root
            for root in roots[1:]
            if (link := links_by_root.get(root)) is None or link.project_id == cloud_id
        ]
        if len(kept) != len(roots):
            entry["root_paths"] = [str(root) for root in kept]
            entry["updated_at"] = now
            moved = True
    return bool(stale) or moved


def _merge_backings(entries: list[dict[str, object]]) -> bool:
    """Keeps one backing per cloud project: a later one's folders join the first as context folders."""
    backings: dict[str, dict[str, object]] = {}
    merged: list[dict[str, object]] = []
    for entry in entries:
        cloud_id = _backing_id(entry)
        if cloud_id is None:
            continue
        backing = backings.setdefault(cloud_id, entry)
        if backing is not entry:
            _absorb(backing, entry)
            merged.append(entry)
    _drop(entries, merged)
    return bool(merged)


def _absorb(backing: dict[str, object], entry: dict[str, object]) -> None:
    """Merges ``entry`` into ``backing``: its folders, and its id for the sessions stamped with it."""
    _add_roots(backing, _raw_roots(entry))
    own_id = entry.get("id")
    absorbed = [*_raw_merged_ids(backing), own_id, *_raw_merged_ids(entry)]
    backing["merged_ids"] = list(
        dict.fromkeys(merged for merged in absorbed if isinstance(merged, str))
    )


def _raw_merged_ids(entry: dict[str, object]) -> list[object]:
    merged_ids = entry.get("merged_ids")
    return merged_ids if isinstance(merged_ids, list) else []


def _add_roots(entry: dict[str, object], roots: Sequence[Path]) -> None:
    entry["root_paths"] = [
        str(root) for root in dict.fromkeys([*_raw_roots(entry), *roots])
    ]


def _drop(
    entries: list[dict[str, object]], dropped: Sequence[dict[str, object]]
) -> None:
    dropped_ids = {id(entry) for entry in dropped}
    entries[:] = [entry for entry in entries if id(entry) not in dropped_ids]


def _backing_id(entry: dict[str, object]) -> str | None:
    project = _parse(entry)
    cloud = project.cloud if project is not None else None
    return cloud.project_id if cloud is not None and cloud.state == "backed" else None


def _cloud_names(entries: list[dict[str, object]]) -> dict[str, str]:
    """The name each cloud project goes by: its backing's."""
    names: dict[str, str] = {}
    for entry in entries:
        cloud_id = _backing_id(entry)
        project = _parse(entry)
        if cloud_id is not None and project is not None:
            names.setdefault(cloud_id, project.name)
    return names


def _link_name(link: ProjectLink, root: Path) -> str:
    return link.project_name.strip() or root.name


def _raw_roots(entry: dict[str, object]) -> list[Path]:
    roots = entry.get("root_paths")
    if not isinstance(roots, list):
        return []
    return [Path(root) for root in roots if isinstance(root, str)]


def _entries(data: dict[str, object]) -> list[dict[str, object]]:
    # Older servers backed a cloud project once per linked folder.
    entries = _raw_entries(data)
    _merge_backings(entries)
    return entries


def _raw_entries(data: dict[str, object]) -> list[dict[str, object]]:
    entries = data.get("projects")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _parse(entry: dict[str, object]) -> LocalProject | None:
    # A project an older server hid is gone, even before the purge drops it.
    if "deleted_at" in entry:
        return None
    try:
        return _ProjectEntry.model_validate(entry).to_project()
    except ValidationError:
        return None


def _find(entries: list[dict[str, object]], project_id: str) -> dict[str, object]:
    for entry in entries:
        if entry.get("id") == project_id and _parse(entry) is not None:
            return entry
    raise LocalProjectNotFound(f"No local project {project_id!r}")


def _hidden(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    # Older servers removed a project by only stamping it.
    return [entry for entry in entries if "deleted_at" in entry]


def _link_keys(entries: Sequence[dict[str, object]]) -> set[tuple[Path, str]]:
    """The folder links that would import one of ``entries``: from one of its folders, to its cloud project."""
    keys: set[tuple[Path, str]] = set()
    for entry in entries:
        cloud = entry.get("cloud")
        cloud_id = cloud.get("project_id") if isinstance(cloud, dict) else None
        if isinstance(cloud_id, str):
            keys.update((root, cloud_id) for root in _raw_roots(entry))
    return keys


def _clean_name(name: str) -> str:
    clean = name.strip()
    if not clean:
        raise LocalProjectError("A project name cannot be empty")
    return clean
