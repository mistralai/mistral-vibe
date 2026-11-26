from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RemoteProjectLink:
    repo_root: Path
    repo_url: str
    project_id: str
    project_name: str


@dataclass(frozen=True)
class LocalProjectLink:
    directory_path: Path
    project_id: str
    project_name: str


type ProjectLink = RemoteProjectLink | LocalProjectLink


def project_link_path(link: ProjectLink) -> Path:
    if isinstance(link, RemoteProjectLink):
        return link.repo_root
    return link.directory_path
