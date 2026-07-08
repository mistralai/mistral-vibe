from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlparse

_SSH_REPO_URL_RE = re.compile(r"^(?:ssh://)?git@(?P<host>[^/:]+)[:/](?P<path>.+)$")


class ProjectMatchKind(StrEnum):
    CURRENT_LINK = auto()
    EXACT_REPO = auto()
    MULTI_REPO = auto()


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
    organization_id: str
    workspace_id: str
    repo_root: Path
    repo_url: str
    project_id: str
    project_name: str


@dataclass(frozen=True)
class ProjectPickerContext:
    organization_id: str
    workspace_id: str
    repo_root: Path
    repo_url: str
    repo_name: str
    saved_link: VibeCodeProjectLink | None = None


@dataclass(frozen=True)
class ProjectPickerProjectItem:
    project: VibeCodeProject
    match_kind: ProjectMatchKind
    label: str
    recommended: bool = False
    kind: Literal["project"] = "project"

    @property
    def option_id(self) -> str:
        return f"project:{self.project.project_id}"


@dataclass(frozen=True)
class ProjectPickerCreateItem:
    project_name: str
    recommended: bool = False
    kind: Literal["create"] = "create"

    @property
    def option_id(self) -> str:
        return "action:create"

    @property
    def label(self) -> str:
        if self.recommended:
            return "recommended"
        return "repo-linked project"


@dataclass(frozen=True)
class ProjectPickerLoadMoreItem:
    kind: Literal["load_more"] = "load_more"
    label: str = ""
    recommended: bool = False

    @property
    def option_id(self) -> str:
        return "action:load_more"


@dataclass(frozen=True)
class ProjectPickerUnlinkItem:
    kind: Literal["unlink"] = "unlink"
    label: str = ""
    recommended: bool = False

    @property
    def option_id(self) -> str:
        return "action:unlink"


ProjectPickerItem = (
    ProjectPickerProjectItem
    | ProjectPickerCreateItem
    | ProjectPickerLoadMoreItem
    | ProjectPickerUnlinkItem
)


def normalize_repo_url(repo_url: str) -> str:
    value = repo_url.strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = f"github.com/{value.removeprefix('git@github.com:')}"
    else:
        parsed = urlparse(value)
        if parsed.netloc and parsed.path:
            value = f"{parsed.netloc}/{parsed.path.lstrip('/')}"

    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value.lower()


def repo_url_label(repo_url: str) -> str:
    value = repo_url.strip().rstrip("/")
    if ssh_match := _SSH_REPO_URL_RE.match(value):
        value = f"{ssh_match.group('host')}/{ssh_match.group('path')}"
    else:
        parsed = urlparse(value)
        if parsed.netloc and parsed.path:
            value = f"{parsed.netloc}/{parsed.path.lstrip('/')}"

    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value


def suggested_project_name(context: ProjectPickerContext) -> str:
    name = context.repo_name.strip()
    if name:
        return name

    repo_path = normalize_repo_url(context.repo_url).rsplit("/", maxsplit=1)[-1]
    return repo_path or "vibe-project"


def build_project_picker_items(
    *,
    context: ProjectPickerContext,
    projects: list[VibeCodeProject],
    query: str = "",
    has_more: bool = False,
    include_unlink: bool = False,
) -> list[ProjectPickerItem]:
    normalized_query = query.strip().casefold()
    visible_projects = [
        project
        for project in projects
        if not project.is_read_only
        and is_project_linked_to_repo(project, context.repo_url)
        and _project_matches_query(project, normalized_query)
    ]

    project_items = [
        _project_item(context=context, project=project) for project in visible_projects
    ]

    primary_items = sorted(project_items, key=_project_sort_key)

    has_primary_recommendation = bool(primary_items)
    create_name = query.strip() or suggested_project_name(context)
    create_item = ProjectPickerCreateItem(
        project_name=create_name,
        recommended=not has_primary_recommendation and not has_more,
    )

    items: list[ProjectPickerItem] = [
        *[
            _with_recommendation(item, recommended=index == 0)
            for index, item in enumerate(primary_items)
        ]
    ]
    if has_more and not primary_items:
        items.append(ProjectPickerLoadMoreItem())
    if has_more and primary_items:
        items.append(ProjectPickerLoadMoreItem())
    items.append(create_item)
    if include_unlink:
        items.append(ProjectPickerUnlinkItem())
    return items


def is_project_linked_to_repo(project: VibeCodeProject, repo_url: str) -> bool:
    current_repo_url = normalize_repo_url(repo_url)
    return any(
        normalize_repo_url(repository.repo_url) == current_repo_url
        for repository in project.repositories
    )


def _project_matches_query(project: VibeCodeProject, normalized_query: str) -> bool:
    if not normalized_query:
        return True

    searchable = [
        project.name,
        *[repository.repo_url for repository in project.repositories],
    ]
    return any(normalized_query in value.casefold() for value in searchable)


def _project_item(
    *, context: ProjectPickerContext, project: VibeCodeProject
) -> ProjectPickerProjectItem:
    match_kind = _match_kind(context=context, project=project)
    return ProjectPickerProjectItem(
        project=project, match_kind=match_kind, label=_label_for_project(match_kind)
    )


def _match_kind(
    *, context: ProjectPickerContext, project: VibeCodeProject
) -> ProjectMatchKind:
    if _is_current_link(context=context, project=project):
        return ProjectMatchKind.CURRENT_LINK

    if len(project.repositories) == 1:
        return ProjectMatchKind.EXACT_REPO
    return ProjectMatchKind.MULTI_REPO


def _is_current_link(
    *, context: ProjectPickerContext, project: VibeCodeProject
) -> bool:
    link = context.saved_link
    if link is None:
        return False
    return link.project_id == project.project_id and normalize_repo_url(
        link.repo_url
    ) == normalize_repo_url(context.repo_url)


def _label_for_project(match_kind: ProjectMatchKind) -> str:
    match match_kind:
        case ProjectMatchKind.CURRENT_LINK:
            return "currently linked"
        case ProjectMatchKind.EXACT_REPO:
            return "exact repo match"
        case ProjectMatchKind.MULTI_REPO:
            return "multi-repo match"


def _project_sort_key(item: ProjectPickerProjectItem) -> tuple[int, str]:
    match item.match_kind:
        case ProjectMatchKind.CURRENT_LINK:
            rank = 0
        case ProjectMatchKind.EXACT_REPO:
            rank = 1
        case ProjectMatchKind.MULTI_REPO:
            rank = 2
    return rank, item.project.name.casefold()


def _with_recommendation(
    item: ProjectPickerProjectItem, *, recommended: bool
) -> ProjectPickerProjectItem:
    return ProjectPickerProjectItem(
        project=item.project,
        match_kind=item.match_kind,
        label=item.label,
        recommended=recommended,
    )
