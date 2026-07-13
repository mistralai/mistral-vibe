from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from vibe.core.vibe_code_project.client import (
    VibeCodeProjectApiError,
    VibeCodeProjectClient,
    VibeCodeProjectPage,
)
from vibe.core.vibe_code_project.project_store import VibeProjectsStore
from vibe.core.vibe_code_project.selection import (
    ProjectPickerContext,
    VibeCodeProject,
    VibeCodeProjectLink,
    is_project_linked_to_repo,
    normalize_repo_url,
    suggested_project_name,
)
from vibe.core.vibe_code_project.telemetry import count_multi_repo_matches

VIBE_CODE_PROJECT_PICKER_PAGE_LIMIT = 100

if TYPE_CHECKING:
    from vibe.core.teleport.git import GitRepoInfo


class VibeCodeProjectPageFetcher(Protocol):
    async def list_projects(
        self, cursor: str | None = None, limit: int | None = None
    ) -> VibeCodeProjectPage: ...

    async def create_project(
        self, *, name: str, repo_url: str, default_branch: str
    ) -> VibeCodeProject: ...


@dataclass(frozen=True)
class VibeCodeProjectPickerState:
    projects: list[VibeCodeProject]
    next_cursor: str | None
    repo_url: str = ""

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


@dataclass(frozen=True)
class VibeCodeProjectPickerInitialData:
    context: ProjectPickerContext
    state: VibeCodeProjectPickerState


@dataclass(frozen=True)
class VibeCodeProjectLoadMoreResult:
    state: VibeCodeProjectPickerState
    focus_project_id: str | None

    @property
    def focus_option_id(self) -> str | None:
        if self.focus_project_id is None:
            return None
        return f"project:{self.focus_project_id}"


@dataclass(frozen=True)
class VibeCodeProjectCreateResult:
    state: VibeCodeProjectPickerState
    project: VibeCodeProject

    @property
    def focus_option_id(self) -> str:
        return f"project:{self.project.project_id}"


@dataclass(frozen=True)
class TeleportProjectResolution:
    project_id: str | None
    initial_data: VibeCodeProjectPickerInitialData
    stale_link_cleared: bool


type HeadlessProjectResolutionSource = Literal[
    "saved_link", "matched_project", "created_project"
]


@dataclass(frozen=True)
class HeadlessProjectResolution:
    project_id: str
    source: HeadlessProjectResolutionSource
    candidate_count_loaded: int = 0
    multi_repo_match_count: int = 0
    saved_project_link_cleared: bool = False
    project_repo_remote_changed: bool = False


class VibeCodeProjectResolverError(Exception):
    pass


class VibeCodeProjectPickerService:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        repo_root: Path,
        page_fetcher: VibeCodeProjectPageFetcher | None = None,
        project_store: VibeProjectsStore | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._repo_root = repo_root
        self._page_fetcher = page_fetcher
        self._project_store = project_store or VibeProjectsStore()
        self._timeout = timeout

    async def load_initial(
        self, git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        context, page = await asyncio.gather(
            self._context_from_git_async(git_info), self._fetch_page()
        )
        return VibeCodeProjectPickerInitialData(
            context=context,
            state=VibeCodeProjectPickerState(
                projects=page.projects,
                next_cursor=page.next_cursor,
                repo_url=git_info.remote_url,
            ),
        )

    async def load_initial_for_teleport(
        self, git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        context = await self._context_from_git_async(git_info)
        if context.saved_link is not None and normalize_repo_url(
            context.saved_link.repo_url
        ) == normalize_repo_url(context.repo_url):
            return VibeCodeProjectPickerInitialData(
                context=context,
                state=VibeCodeProjectPickerState(
                    projects=[], next_cursor=None, repo_url=git_info.remote_url
                ),
            )
        page = await self._fetch_page()
        return VibeCodeProjectPickerInitialData(
            context=context,
            state=VibeCodeProjectPickerState(
                projects=page.projects,
                next_cursor=page.next_cursor,
                repo_url=git_info.remote_url,
            ),
        )

    async def load_more(
        self, state: VibeCodeProjectPickerState
    ) -> VibeCodeProjectLoadMoreResult:
        cursor = state.next_cursor
        projects = list(state.projects)
        next_cursor = state.next_cursor
        focus_project_id: str | None = None

        while cursor is not None:
            page = await self._fetch_page(cursor=cursor)
            projects.extend(page.projects)
            next_cursor = page.next_cursor

            # Read-only and non-repo-linked projects are hidden by the picker, so
            # keep paging until a newly visible/selectable project is available.
            new_selectable_project = next(
                (
                    project
                    for project in page.projects
                    if not project.is_read_only
                    and _is_project_visible_in_picker(project, state.repo_url)
                ),
                None,
            )
            if new_selectable_project is not None:
                focus_project_id = new_selectable_project.project_id
                break

            cursor = page.next_cursor

        return VibeCodeProjectLoadMoreResult(
            state=VibeCodeProjectPickerState(
                projects=projects, next_cursor=next_cursor, repo_url=state.repo_url
            ),
            focus_project_id=focus_project_id,
        )

    async def create_project(
        self,
        *,
        name: str,
        default_branch: str,
        git_info: GitRepoInfo,
        state: VibeCodeProjectPickerState,
    ) -> VibeCodeProjectCreateResult:
        normalized_name = name.strip()
        if not normalized_name:
            raise VibeCodeProjectApiError("Project name cannot be empty.")
        normalized_default_branch = default_branch.strip()
        if not normalized_default_branch:
            raise VibeCodeProjectApiError("Default branch cannot be empty.")

        project = await self._create_project(
            name=normalized_name,
            repo_url=git_info.remote_url,
            default_branch=normalized_default_branch,
        )
        projects = [
            existing
            for existing in state.projects
            if existing.project_id != project.project_id
        ]
        return VibeCodeProjectCreateResult(
            state=VibeCodeProjectPickerState(
                projects=[project, *projects],
                next_cursor=state.next_cursor,
                repo_url=state.repo_url,
            ),
            project=project,
        )

    def save_project_link(
        self, *, context: ProjectPickerContext, project_id: str, project_name: str
    ) -> VibeCodeProjectLink:
        link = VibeCodeProjectLink(
            repo_root=context.repo_root,
            repo_url=context.repo_url,
            project_id=project_id,
            project_name=project_name,
        )
        self._project_store.upsert_remote_project(link)
        return link

    def clear_project_link(self, context: ProjectPickerContext) -> None:
        self._project_store.delete_remote_project(repo_root=context.repo_root)

    async def _save_project_link_async(
        self, *, context: ProjectPickerContext, project_id: str, project_name: str
    ) -> VibeCodeProjectLink:
        return await asyncio.to_thread(
            self.save_project_link,
            context=context,
            project_id=project_id,
            project_name=project_name,
        )

    async def _clear_project_link_async(self, context: ProjectPickerContext) -> None:
        await asyncio.to_thread(self.clear_project_link, context)

    def resolve_project_for_teleport(
        self, initial_data: VibeCodeProjectPickerInitialData
    ) -> TeleportProjectResolution:
        saved_link = initial_data.context.saved_link
        if saved_link is not None and normalize_repo_url(
            saved_link.repo_url
        ) == normalize_repo_url(initial_data.context.repo_url):
            return TeleportProjectResolution(
                project_id=saved_link.project_id,
                initial_data=initial_data,
                stale_link_cleared=False,
            )

        if saved_link is not None:
            self.clear_project_link(initial_data.context)
            initial_data = VibeCodeProjectPickerInitialData(
                context=ProjectPickerContext(
                    repo_root=initial_data.context.repo_root,
                    repo_url=initial_data.context.repo_url,
                    repo_name=initial_data.context.repo_name,
                    saved_link=None,
                ),
                state=initial_data.state,
            )
            return TeleportProjectResolution(
                project_id=None, initial_data=initial_data, stale_link_cleared=True
            )

        return TeleportProjectResolution(
            project_id=None, initial_data=initial_data, stale_link_cleared=False
        )

    async def resolve_project_for_headless_teleport(
        self, git_info: GitRepoInfo
    ) -> HeadlessProjectResolution:
        context = await self._context_from_git_async(git_info)
        saved_project_link_cleared = False
        project_repo_remote_changed = False
        if context.saved_link is not None:
            if normalize_repo_url(context.saved_link.repo_url) == normalize_repo_url(
                context.repo_url
            ):
                return HeadlessProjectResolution(
                    project_id=context.saved_link.project_id, source="saved_link"
                )
            await self._clear_project_link_async(context)
            saved_project_link_cleared = True
            project_repo_remote_changed = True
            context = ProjectPickerContext(
                repo_root=context.repo_root,
                repo_url=context.repo_url,
                repo_name=context.repo_name,
                saved_link=None,
            )

        projects = await self._fetch_all_projects()
        multi_repo_match_count = count_multi_repo_matches(projects, context.repo_url)
        exact_matching_projects = [
            project
            for project in projects
            if not project.is_read_only
            and is_project_linked_to_repo(project, context.repo_url)
            and len(project.repositories) == 1
        ]

        if len(exact_matching_projects) == 1:
            project = exact_matching_projects[0]
            await self._save_project_link_async(
                context=context,
                project_id=project.project_id,
                project_name=project.name,
            )
            return HeadlessProjectResolution(
                project_id=project.project_id,
                source="matched_project",
                candidate_count_loaded=len(projects),
                multi_repo_match_count=multi_repo_match_count,
                saved_project_link_cleared=saved_project_link_cleared,
                project_repo_remote_changed=project_repo_remote_changed,
            )

        default_branch = git_info.default_branch or git_info.branch
        if default_branch is None:
            raise VibeCodeProjectResolverError(
                "Could not determine the repository default branch. "
                "Check out a branch and try again."
            )

        project = await self._create_project(
            name=suggested_project_name(context),
            repo_url=context.repo_url,
            default_branch=default_branch,
        )
        await self._save_project_link_async(
            context=context, project_id=project.project_id, project_name=project.name
        )
        return HeadlessProjectResolution(
            project_id=project.project_id,
            source="created_project",
            candidate_count_loaded=len(projects),
            multi_repo_match_count=multi_repo_match_count,
            saved_project_link_cleared=saved_project_link_cleared,
            project_repo_remote_changed=project_repo_remote_changed,
        )

    async def _fetch_all_projects(self) -> list[VibeCodeProject]:
        projects: list[VibeCodeProject] = []
        cursor: str | None = None
        while True:
            page = await self._fetch_page(cursor=cursor)
            projects.extend(page.projects)
            if page.next_cursor is None:
                return projects
            cursor = page.next_cursor

    async def _fetch_page(self, cursor: str | None = None) -> VibeCodeProjectPage:
        return await self._with_client(
            lambda client: client.list_projects(
                cursor=cursor, limit=VIBE_CODE_PROJECT_PICKER_PAGE_LIMIT
            )
        )

    async def _create_project(
        self, *, name: str, repo_url: str, default_branch: str
    ) -> VibeCodeProject:
        return await self._with_client(
            lambda client: client.create_project(
                name=name, repo_url=repo_url, default_branch=default_branch
            )
        )

    async def _with_client[T](
        self, operation: Callable[[VibeCodeProjectPageFetcher], Awaitable[T]]
    ) -> T:
        if not self._api_key:
            raise VibeCodeProjectApiError("Vibe Code Web API key not set.")

        if self._page_fetcher is not None:
            return await operation(self._page_fetcher)

        async with VibeCodeProjectClient(
            self._base_url, self._api_key, timeout=self._timeout
        ) as client:
            return await operation(client)

    def _context_from_git(self, git_info: GitRepoInfo) -> ProjectPickerContext:
        repo_root = git_info.repo_root or self._repo_root
        return ProjectPickerContext(
            repo_root=repo_root,
            repo_url=git_info.remote_url,
            repo_name=git_info.repo,
            saved_link=self._project_store.get_remote_project(repo_root=repo_root),
        )

    async def _context_from_git_async(
        self, git_info: GitRepoInfo
    ) -> ProjectPickerContext:
        return await asyncio.to_thread(self._context_from_git, git_info)


def _is_project_visible_in_picker(project: VibeCodeProject, repo_url: str) -> bool:
    if not repo_url:
        return True
    return is_project_linked_to_repo(project, repo_url)
