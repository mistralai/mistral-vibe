from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from vibe.core.vibe_code_project.client import (
    VibeCodeProjectApiError,
    VibeCodeProjectClient,
    VibeCodeProjectPage,
)
from vibe.core.vibe_code_project.selection import (
    ProjectPickerContext,
    VibeCodeProject,
    is_project_linked_to_repo,
)

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


class VibeCodeProjectPickerService:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        repo_root: Path,
        page_fetcher: VibeCodeProjectPageFetcher | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._repo_root = repo_root
        self._page_fetcher = page_fetcher
        self._timeout = timeout

    async def load_initial(
        self, git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        page = await self._fetch_page()
        return VibeCodeProjectPickerInitialData(
            context=self._context_from_git(git_info),
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
        return ProjectPickerContext(
            organization_id="",
            workspace_id="",
            repo_root=self._repo_root,
            repo_url=git_info.remote_url,
            repo_name=git_info.repo,
        )


def _is_project_visible_in_picker(project: VibeCodeProject, repo_url: str) -> bool:
    if not repo_url:
        return True
    return is_project_linked_to_repo(project, repo_url)
