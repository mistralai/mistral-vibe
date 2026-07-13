from __future__ import annotations

from pathlib import Path
import threading

import pytest

from vibe.core.teleport.git import GitRepoInfo
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    ProjectRepository,
    TeleportProjectResolution,
    VibeCodeProject,
    VibeCodeProjectApiError,
    VibeCodeProjectLink,
    VibeCodeProjectPage,
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
    VibeProjectsStore,
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


class TrackingProjectsStore(VibeProjectsStore):
    def __init__(self, path: Path | str | None = None) -> None:
        super().__init__(path)
        self.get_remote_project_threads: list[int] = []
        self.upsert_remote_project_threads: list[int] = []
        self.delete_remote_project_threads: list[int] = []

    def get_remote_project(self, *, repo_root: Path) -> VibeCodeProjectLink | None:
        self.get_remote_project_threads.append(threading.get_ident())
        return super().get_remote_project(repo_root=repo_root)

    def upsert_remote_project(self, link: VibeCodeProjectLink) -> None:
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
async def test_load_initial_includes_saved_project_link(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    saved_link = VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        project_id="mistral-vibe",
        project_name="Mistral Vibe",
    )
    store.upsert_remote_project(saved_link)
    fetcher = FakePageFetcher([VibeCodeProjectPage(projects=[], next_cursor=None)])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
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
        VibeCodeProjectLink(
            repo_root=repo_root,
            repo_url="https://github.com/mistralai/mistral-vibe.git",
            project_id="mistral-vibe",
            project_name="Mistral Vibe",
        )
    )
    store.reset_thread_records()
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
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
async def test_load_initial_for_teleport_skips_fetch_with_valid_saved_link(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    saved_link = VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        project_id="mistral-vibe",
        project_name="Mistral Vibe",
    )
    store.upsert_remote_project(saved_link)
    fetcher = FakePageFetcher([])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    initial = await service.load_initial_for_teleport(_git_info())

    assert fetcher.calls == []
    assert initial.context.saved_link == saved_link
    assert initial.state.projects == []


@pytest.mark.asyncio
async def test_load_initial_for_teleport_fetches_when_no_saved_link(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
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
        repo_root=repo_root,
        page_fetcher=fetcher,
    )

    initial = await service.load_initial_for_teleport(_git_info())

    assert fetcher.calls == [(None, 100)]
    assert initial.state.projects[0].project_id == "mistral-vibe"
    assert initial.state.has_more is True


@pytest.mark.asyncio
async def test_load_initial_for_teleport_fetches_when_saved_link_for_different_repo(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    stale_link = VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/other/repo.git",
        project_id="other-project",
        project_name="Other Project",
    )
    store.upsert_remote_project(stale_link)
    fetcher = FakePageFetcher([
        VibeCodeProjectPage(
            projects=[
                _project(
                    "mistral-vibe",
                    "Mistral Vibe",
                    "https://github.com/mistralai/mistral-vibe.git",
                )
            ],
            next_cursor=None,
        )
    ])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    initial = await service.load_initial_for_teleport(_git_info())

    assert fetcher.calls == [(None, 100)]
    assert initial.context.saved_link == stale_link
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


def _initial_data(
    repo_root: Path,
    repo_url: str = "https://github.com/mistralai/mistral-vibe.git",
    saved_link: VibeCodeProjectLink | None = None,
) -> VibeCodeProjectPickerInitialData:
    return VibeCodeProjectPickerInitialData(
        context=ProjectPickerContext(
            repo_root=repo_root,
            repo_url=repo_url,
            repo_name="mistral-vibe",
            saved_link=saved_link,
        ),
        state=VibeCodeProjectPickerState(projects=[], next_cursor=None),
    )


def _link(
    repo_root: Path,
    repo_url: str = "https://github.com/mistralai/mistral-vibe.git",
    project_id: str = "proj-123",
) -> VibeCodeProjectLink:
    return VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url=repo_url,
        project_id=project_id,
        project_name="Mistral Vibe",
    )


def test_resolve_project_for_teleport_returns_saved_project_id_when_urls_match(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    saved = _link(repo_root)
    store.upsert_remote_project(saved)
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=FakePageFetcher([]),
        project_store=store,
    )

    resolution = service.resolve_project_for_teleport(
        _initial_data(repo_root, saved_link=saved)
    )

    assert resolution == TeleportProjectResolution(
        project_id="proj-123",
        initial_data=_initial_data(repo_root, saved_link=saved),
        stale_link_cleared=False,
    )
    assert store.get_remote_project(repo_root=repo_root) is not None


def test_resolve_project_for_teleport_clears_stale_link_when_url_differs(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    stale = _link(repo_root, repo_url="https://github.com/other/repo.git")
    store.upsert_remote_project(stale)
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=FakePageFetcher([]),
        project_store=store,
    )
    initial = _initial_data(
        repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        saved_link=stale,
    )

    resolution = service.resolve_project_for_teleport(initial)

    assert resolution.project_id is None
    assert resolution.stale_link_cleared is True
    assert resolution.initial_data.context.saved_link is None
    assert store.get_remote_project(repo_root=repo_root) is None


def test_resolve_project_for_teleport_returns_none_when_no_saved_link(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=FakePageFetcher([]),
    )

    resolution = service.resolve_project_for_teleport(_initial_data(repo_root))

    assert resolution.project_id is None
    assert resolution.stale_link_cleared is False
    assert resolution.initial_data.context.saved_link is None


@pytest.mark.asyncio
async def test_headless_resolution_uses_matching_saved_link(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    saved = _link(repo_root)
    store.upsert_remote_project(saved)
    fetcher = FakePageFetcher([])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    resolution = await service.resolve_project_for_headless_teleport(_git_info())

    assert resolution.project_id == "proj-123"
    assert resolution.source == "saved_link"
    assert fetcher.calls == []


@pytest.mark.asyncio
async def test_headless_resolution_uses_single_exact_matching_project_and_saves_link(
    tmp_path: Path,
) -> None:
    event_loop_thread = threading.get_ident()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = TrackingProjectsStore(tmp_path / "projects.toml")
    fetcher = FakePageFetcher([
        VibeCodeProjectPage(
            projects=[
                _project("other", "Other", "https://github.com/mistralai/other.git")
            ],
            next_cursor="next-page",
        ),
        VibeCodeProjectPage(
            projects=[
                _project(
                    "mistral-vibe",
                    "Mistral Vibe",
                    "https://github.com/mistralai/mistral-vibe.git",
                )
            ],
            next_cursor=None,
        ),
    ])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    resolution = await service.resolve_project_for_headless_teleport(_git_info())

    assert resolution.project_id == "mistral-vibe"
    assert resolution.source == "matched_project"
    assert fetcher.calls == [(None, 100), ("next-page", 100)]
    assert store.upsert_remote_project_threads
    assert all(
        thread_id != event_loop_thread
        for thread_id in store.upsert_remote_project_threads
    )
    assert store.get_remote_project(repo_root=repo_root) == VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        project_id="mistral-vibe",
        project_name="Mistral Vibe",
    )


@pytest.mark.asyncio
async def test_headless_resolution_creates_project_when_no_match(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    fetcher = FakePageFetcher([VibeCodeProjectPage(projects=[], next_cursor=None)])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    resolution = await service.resolve_project_for_headless_teleport(_git_info())

    assert resolution.project_id == "created"
    assert resolution.source == "created_project"
    assert fetcher.created == [
        ("mistral-vibe", "https://github.com/mistralai/mistral-vibe.git", "main")
    ]
    assert store.get_remote_project(repo_root=repo_root) == VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        project_id="created",
        project_name="mistral-vibe",
    )


@pytest.mark.asyncio
async def test_headless_resolution_creates_project_for_single_multi_repo_match(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    fetcher = FakePageFetcher([
        VibeCodeProjectPage(
            projects=[
                _multi_repo_project(
                    "multi",
                    "Multi Repo",
                    "https://github.com/mistralai/mistral-vibe.git",
                    "https://github.com/mistralai/other.git",
                )
            ],
            next_cursor=None,
        )
    ])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    resolution = await service.resolve_project_for_headless_teleport(_git_info())

    assert resolution.project_id == "created"
    assert resolution.source == "created_project"
    assert fetcher.created == [
        ("mistral-vibe", "https://github.com/mistralai/mistral-vibe.git", "main")
    ]
    assert store.get_remote_project(repo_root=repo_root) == VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        project_id="created",
        project_name="mistral-vibe",
    )


@pytest.mark.asyncio
async def test_headless_resolution_creates_project_when_matches_are_ambiguous(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    store = VibeProjectsStore(tmp_path / "projects.toml")
    fetcher = FakePageFetcher([
        VibeCodeProjectPage(
            projects=[
                _project("one", "One", "https://github.com/mistralai/mistral-vibe.git"),
                _project("two", "Two", "https://github.com/mistralai/mistral-vibe.git"),
            ],
            next_cursor=None,
        )
    ])
    service = VibeCodeProjectPickerService(
        base_url="https://chat.example.com",
        api_key="api-key",
        repo_root=repo_root,
        page_fetcher=fetcher,
        project_store=store,
    )

    resolution = await service.resolve_project_for_headless_teleport(_git_info())

    assert resolution.project_id == "created"
    assert resolution.source == "created_project"
    assert fetcher.created == [
        ("mistral-vibe", "https://github.com/mistralai/mistral-vibe.git", "main")
    ]
    assert store.get_remote_project(repo_root=repo_root) == VibeCodeProjectLink(
        repo_root=repo_root,
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        project_id="created",
        project_name="mistral-vibe",
    )
