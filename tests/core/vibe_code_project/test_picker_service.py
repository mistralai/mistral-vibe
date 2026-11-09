from __future__ import annotations

from pathlib import Path
import threading

import pytest

from vibe.core.git.repository import GitRepoInfo
from vibe.core.platform_auth import ApiKeyPlatformCredentialProvider
from vibe.core.vibe_code_project import (
    ProjectRepository,
    RemoteProjectLink,
    VibeCodeProject,
    VibeCodeProjectApiError,
    VibeCodeProjectPage,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
    VibeCodeProjectResolverError,
    VibeProjectsStore,
)


class FakePageFetcher:
    def __init__(self, pages: list[VibeCodeProjectPage]) -> None:
        self.pages = pages
        self.calls: list[tuple[str | None, int | None]] = []
        self.created: list[tuple[str, str | None, str | None]] = []

    async def list_projects(
        self, cursor: str | None = None, limit: int | None = None
    ) -> VibeCodeProjectPage:
        self.calls.append((cursor, limit))
        return self.pages.pop(0)

    async def create_project(
        self,
        *,
        name: str,
        repo_url: str | None = None,
        default_branch: str | None = None,
    ) -> VibeCodeProject:
        self.created.append((name, repo_url, default_branch))
        return _project("created", name, repo_url or "")


class TrackingProjectsStore(VibeProjectsStore):
    def __init__(self, path: Path | str | None = None) -> None:
        super().__init__(path)
        self.get_remote_project_threads: list[int] = []
        self.upsert_remote_project_threads: list[int] = []
        self.delete_remote_project_threads: list[int] = []

    def get_remote_project(self, *, repo_root: Path) -> RemoteProjectLink | None:
        self.get_remote_project_threads.append(threading.get_ident())
        return super().get_remote_project(repo_root=repo_root)

    def upsert_remote_project(self, link: RemoteProjectLink) -> None:
        self.upsert_remote_project_threads.append(threading.get_ident())
        super().upsert_remote_project(link)

    def delete_remote_project(self, *, repo_root: Path) -> None:
        self.delete_remote_project_threads.append(threading.get_ident())
        super().delete_remote_project(repo_root=repo_root)

    def reset_thread_records(self) -> None:
        self.get_remote_project_threads.clear()
        self.upsert_remote_project_threads.clear()
        self.delete_remote_project_threads.clear()


def _project(
    project_id: str, name: str, repo_url: str, *, is_read_only: bool = False
) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=(ProjectRepository(repo_url=repo_url),),
        is_read_only=is_read_only,
    )


def _multi_repo_project(project_id: str, name: str, *repo_urls: str) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=tuple(
            ProjectRepository(repo_url=repo_url) for repo_url in repo_urls
        ),
    )


def _git_info() -> GitRepoInfo:
    return GitRepoInfo(
        remote_name="origin",
        remote_url="https://github.com/mistralai/mistral-vibe.git",
        owner="mistralai",
        repo="mistral-vibe",
        branch="feature-branch",
        commit="abc123",
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
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("api-key"),
        repo_root=Path("/repo/mistral-vibe"),
        page_fetcher=fetcher,
    )

    initial = await service.load_initial(_git_info())

    assert fetcher.calls == [(None, 100)]
    assert initial.context.repo_name == "mistral-vibe"
    assert initial.context.repo_url == "https://github.com/mistralai/mistral-vibe.git"
    assert initial.state.next_cursor is not None
    assert initial.state.projects[0].project_id == "mistral-vibe"


@pytest.mark.asyncio
async def test_load_initial_includes_saved_project_link(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    saved_link = RemoteProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        project_id="mistral-vibe",
        project_name="Mistral Vibe",
    )
    store.upsert_remote_project(saved_link)
    fetcher = FakePageFetcher([VibeCodeProjectPage(projects=[], next_cursor=None)])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("api-key"),
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    initial = await service.load_initial(_git_info())

    assert initial.context.saved_link == saved_link


@pytest.mark.asyncio
async def test_load_initial_reads_saved_project_link_off_event_loop(
    tmp_path: Path,
) -> None:
    event_loop_thread = threading.get_ident()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = TrackingProjectsStore(tmp_path / "projects.toml")
    store.upsert_remote_project(
        RemoteProjectLink(
            repo_root=repo_root,
            repo_url="https://github.com/mistralai/mistral-vibe.git",
            project_id="mistral-vibe",
            project_name="Mistral Vibe",
        )
    )
    store.reset_thread_records()
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("api-key"),
        repo_root=repo_root,
        page_fetcher=FakePageFetcher([
            VibeCodeProjectPage(projects=[], next_cursor=None)
        ]),
        project_store=store,
    )

    await service.load_initial(_git_info())

    assert store.get_remote_project_threads
    assert all(
        thread_id != event_loop_thread for thread_id in store.get_remote_project_threads
    )


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
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("api-key"),
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
    assert result.state.next_cursor is None
    assert result.focus_project_id == "docs"


@pytest.mark.asyncio
async def test_create_project_uses_git_repo_and_prepends_created_project() -> None:
    existing = _project(
        "mistral-vibe", "Mistral Vibe", "https://github.com/mistralai/mistral-vibe.git"
    )
    fetcher = FakePageFetcher([])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("api-key"),
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
    assert result.project.project_id == "created"
    assert [project.project_id for project in result.state.projects] == [
        "created",
        "mistral-vibe",
    ]
    assert result.state.next_cursor == "next"


@pytest.mark.asyncio
async def test_create_project_requires_default_branch() -> None:
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("api-key"),
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


_LINK_REPO = "https://github.com/mistralai/mistral-vibe.git"


def _link_service(projects: list[VibeCodeProject]) -> VibeCodeProjectPickerService:
    return VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        credentials=ApiKeyPlatformCredentialProvider.from_api_key("api-key"),
        repo_root=Path("/repo/mistral-vibe"),
        page_fetcher=FakePageFetcher([
            VibeCodeProjectPage(projects=projects, next_cursor=None)
        ]),
    )


@pytest.mark.asyncio
async def test_find_linkable_project_returns_validated_project() -> None:
    service = _link_service([_project("p1", "Mistral Vibe", _LINK_REPO)])

    project = await service.find_linkable_project(project_id="p1", repo_url=_LINK_REPO)

    assert project.project_id == "p1"


@pytest.mark.asyncio
async def test_find_linkable_project_rejects_unknown_project() -> None:
    service = _link_service([_project("p1", "Mistral Vibe", _LINK_REPO)])

    with pytest.raises(VibeCodeProjectResolverError):
        await service.find_linkable_project(project_id="missing", repo_url=_LINK_REPO)


@pytest.mark.asyncio
async def test_find_linkable_project_rejects_read_only_or_unlinked() -> None:
    read_only = _link_service([
        _project("ro", "Read only", _LINK_REPO, is_read_only=True)
    ])
    with pytest.raises(VibeCodeProjectResolverError):
        await read_only.find_linkable_project(project_id="ro", repo_url=_LINK_REPO)

    unlinked = _link_service([
        _project("other", "Other repo", "https://github.com/mistralai/other.git")
    ])
    with pytest.raises(VibeCodeProjectResolverError):
        await unlinked.find_linkable_project(project_id="other", repo_url=_LINK_REPO)
