from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.teleport.git import GitRepoInfo
from vibe.core.vibe_code_project import (
    ProjectRepository,
    VibeCodeProject,
    VibeCodeProjectApiError,
    VibeCodeProjectPage,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
)


class FakePageFetcher:
    def __init__(self, pages: list[VibeCodeProjectPage]) -> None:
        self.pages = pages
        self.calls: list[tuple[str | None, int | None]] = []
        self.created: list[tuple[str, str, str]] = []

    async def list_projects(
        self, cursor: str | None = None, limit: int | None = None
    ) -> VibeCodeProjectPage:
        self.calls.append((cursor, limit))
        return self.pages.pop(0)

    async def create_project(
        self, *, name: str, repo_url: str, default_branch: str
    ) -> VibeCodeProject:
        self.created.append((name, repo_url, default_branch))
        return _project("created", name, repo_url)


def _project(
    project_id: str, name: str, repo_url: str, *, is_read_only: bool = False
) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=(ProjectRepository(repo_url=repo_url),),
        is_read_only=is_read_only,
    )


def _git_info() -> GitRepoInfo:
    return GitRepoInfo(
        remote_name="origin",
        remote_url="https://github.com/mistralai/mistral-vibe.git",
        owner="mistralai",
        repo="mistral-vibe",
        branch="feature-branch",
        commit="abc123",
        diff="",
        default_branch="main",
    )


@pytest.mark.asyncio
async def test_load_initial_builds_context_and_fetches_first_page() -> None:
    fetcher = FakePageFetcher([
        VibeCodeProjectPage(
            projects=[
                _project(
                    "mistral-vibe",
                    "Mistral Vibe",
                    "https://github.com/mistralai/mistral-vibe.git",
                )
            ],
            next_cursor="next-page",
        )
    ])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=Path("/repo/mistral-vibe"),
        page_fetcher=fetcher,
    )

    initial = await service.load_initial(_git_info())

    assert fetcher.calls == [(None, 100)]
    assert initial.context.repo_name == "mistral-vibe"
    assert initial.context.repo_url == "https://github.com/mistralai/mistral-vibe.git"
    assert initial.state.has_more is True
    assert initial.state.projects[0].project_id == "mistral-vibe"


@pytest.mark.asyncio
async def test_load_more_skips_until_new_selectable_project() -> None:
    existing = _project(
        "mistral-vibe", "Mistral Vibe", "https://github.com/mistralai/mistral-vibe.git"
    )
    fetcher = FakePageFetcher([
        VibeCodeProjectPage(
            projects=[
                _project(
                    "read-only",
                    "Read Only",
                    "https://github.com/mistralai/read-only.git",
                    is_read_only=True,
                )
            ],
            next_cursor="final-page",
        ),
        VibeCodeProjectPage(
            projects=[
                _project(
                    "docs", "Docs", "https://github.com/mistralai/mistral-vibe.git"
                )
            ],
            next_cursor=None,
        ),
    ])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=Path("/repo/mistral-vibe"),
        page_fetcher=fetcher,
    )

    result = await service.load_more(
        VibeCodeProjectPickerState(
            projects=[existing],
            next_cursor="next-page",
            repo_url="https://github.com/mistralai/mistral-vibe.git",
        )
    )

    assert fetcher.calls == [("next-page", 100), ("final-page", 100)]
    assert [project.project_id for project in result.state.projects] == [
        "mistral-vibe",
        "read-only",
        "docs",
    ]
    assert result.state.has_more is False
    assert result.focus_option_id == "project:docs"


@pytest.mark.asyncio
async def test_create_project_uses_git_repo_and_prepends_created_project() -> None:
    existing = _project(
        "mistral-vibe", "Mistral Vibe", "https://github.com/mistralai/mistral-vibe.git"
    )
    fetcher = FakePageFetcher([])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=Path("/repo/mistral-vibe"),
        page_fetcher=fetcher,
    )

    result = await service.create_project(
        name="  Custom Mistral Vibe  ",
        default_branch="  main  ",
        git_info=_git_info(),
        state=VibeCodeProjectPickerState(projects=[existing], next_cursor="next"),
    )

    assert fetcher.created == [
        ("Custom Mistral Vibe", "https://github.com/mistralai/mistral-vibe.git", "main")
    ]
    assert result.project.name == "Custom Mistral Vibe"
    assert result.focus_option_id == "project:created"
    assert [project.project_id for project in result.state.projects] == [
        "created",
        "mistral-vibe",
    ]
    assert result.state.next_cursor == "next"


@pytest.mark.asyncio
async def test_create_project_requires_default_branch() -> None:
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=Path("/repo/mistral-vibe"),
        page_fetcher=FakePageFetcher([]),
    )

    with pytest.raises(VibeCodeProjectApiError, match="Default branch"):
        await service.create_project(
            name="Mistral Vibe",
            default_branch=" ",
            git_info=_git_info(),
            state=VibeCodeProjectPickerState(projects=[], next_cursor=None),
        )
