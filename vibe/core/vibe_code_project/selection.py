from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibe.utils.repository import normalize_repo_url


def is_saved_project_stale_error(message: str) -> bool:
    normalized = message.casefold()
    return (
        "project not found" in normalized
        or ("status 404" in normalized and "project" in normalized)
        or ("status 403" in normalized and "project" in normalized)
        or ("forbidden" in normalized and "project" in normalized)
    )


@dataclass(frozen=True)
class ProjectRepository:
    repo_url: str
    default_branch: str | None = None


@dataclass(frozen=True)
class VibeCodeProject:
    project_id: str
    name: str
    repositories: tuple[ProjectRepository, ...] = ()
    is_read_only: bool = False


@dataclass(frozen=True)
class VibeCodeProjectLink:
    repo_root: Path
    repo_url: str
    project_id: str
    project_name: str


@dataclass(frozen=True)
class ProjectPickerContext:
    repo_root: Path
    repo_url: str
    repo_name: str
    saved_link: VibeCodeProjectLink | None = None


def suggested_project_name(context: ProjectPickerContext) -> str:
    name = context.repo_name.strip()
    if name:
        return name

    repo_path = normalize_repo_url(context.repo_url).rsplit("/", maxsplit=1)[-1]
    return repo_path or "vibe-project"


def is_project_linked_to_repo(project: VibeCodeProject, repo_url: str) -> bool:
    current_repo_url = normalize_repo_url(repo_url)
    return any(
        normalize_repo_url(repository.repo_url) == current_repo_url
        for repository in project.repositories
    )


def _project_matches_query(project: VibeCodeProject, normalized_query: str) -> bool:
    if not normalized_query:
        return True
    return normalized_query in project.name.casefold()


def _match_rank(project: VibeCodeProject, context: ProjectPickerContext) -> int:
    # Ranking priority: the currently linked project first, then exact-repo
    # (single-repo) matches, then multi-repo matches.
    saved_link = context.saved_link
    if (
        saved_link is not None
        and saved_link.project_id == project.project_id
        and normalize_repo_url(saved_link.repo_url)
        == normalize_repo_url(context.repo_url)
    ):
        return 0
    return 1 if len(project.repositories) == 1 else 2


def rank_project_items(
    *, context: ProjectPickerContext, projects: list[VibeCodeProject], query: str = ""
) -> list[VibeCodeProject]:
    """Visible, ranked projects for a picker context.

    Filters to writable projects linked to the context repo (optionally matching
    a query), then orders them the way the terminal picker does: the currently
    linked project first, then exact-repo matches, then multi-repo matches, each
    by name. The ACP/Desktop picker uses this to recommend the same project the
    terminal UI would.
    """
    normalized_query = query.strip().casefold()
    visible_projects = [
        project
        for project in projects
        if not project.is_read_only
        and is_project_linked_to_repo(project, context.repo_url)
        and _project_matches_query(project, normalized_query)
    ]
    return sorted(
        visible_projects,
        key=lambda project: (_match_rank(project, context), project.name.casefold()),
    )
