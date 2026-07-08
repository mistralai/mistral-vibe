from __future__ import annotations

from pathlib import Path

from vibe.core.vibe_code_project import (
    ProjectMatchKind,
    ProjectPickerContext,
    ProjectPickerCreateItem,
    ProjectPickerLoadMoreItem,
    ProjectPickerProjectItem,
    ProjectPickerUnlinkItem,
    ProjectRepository,
    VibeCodeProject,
    VibeCodeProjectLink,
    build_project_picker_items,
    normalize_repo_url,
    repo_url_label,
    suggested_project_name,
)

CURRENT_REPO_URL = "https://github.com/mistralai/mistral-vibe.git"


def _context(saved_link: VibeCodeProjectLink | None = None) -> ProjectPickerContext:
    return ProjectPickerContext(
        organization_id="org",
        workspace_id="workspace",
        repo_root=Path("/repo/mistral-vibe"),
        repo_url=CURRENT_REPO_URL,
        repo_name="mistral-vibe",
        saved_link=saved_link,
    )


def _link(project_id: str, repo_url: str = CURRENT_REPO_URL) -> VibeCodeProjectLink:
    return VibeCodeProjectLink(
        organization_id="org",
        workspace_id="workspace",
        repo_root=Path("/repo/mistral-vibe"),
        repo_url=repo_url,
        project_id=project_id,
        project_name="Mistral Vibe",
    )


def _project(
    project_id: str, name: str, *repo_urls: str, is_read_only: bool = False
) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=tuple(ProjectRepository(repo_url=url) for url in repo_urls),
        is_read_only=is_read_only,
    )


def test_normalize_repo_url_matches_common_github_forms() -> None:
    assert normalize_repo_url("https://github.com/MistralAI/mistral-vibe.git") == (
        "github.com/mistralai/mistral-vibe"
    )
    assert normalize_repo_url("git@github.com:mistralai/mistral-vibe.git") == (
        "github.com/mistralai/mistral-vibe"
    )
    assert normalize_repo_url("https://github.com/mistralai/mistral-vibe/") == (
        "github.com/mistralai/mistral-vibe"
    )


def test_repo_url_label_strips_transport_without_assuming_provider() -> None:
    assert repo_url_label("https://github.com/MistralAI/mistral-vibe.git") == (
        "github.com/MistralAI/mistral-vibe"
    )
    assert repo_url_label("git@github.com:mistralai/mistral-vibe.git") == (
        "github.com/mistralai/mistral-vibe"
    )
    assert repo_url_label("ssh://git@gitlab.com/mistralai/mistral-vibe.git") == (
        "gitlab.com/mistralai/mistral-vibe"
    )


def test_suggested_project_name_prefers_repo_name() -> None:
    assert suggested_project_name(_context()) == "mistral-vibe"


def test_ranks_current_link_and_repo_matches_before_actions() -> None:
    items = build_project_picker_items(
        context=_context(saved_link=_link("linked")),
        projects=[
            _project("other", "Internal Tools", "https://github.com/mistralai/tools"),
            _project(
                "multi",
                "Mistral Vibe + Docs",
                CURRENT_REPO_URL,
                "https://github.com/mistralai/docs",
            ),
            _project("exact", "Mistral Vibe API", CURRENT_REPO_URL),
            _project("linked", "Mistral Vibe", CURRENT_REPO_URL),
        ],
        has_more=True,
        include_unlink=True,
    )

    assert [item.option_id for item in items] == [
        "project:linked",
        "project:exact",
        "project:multi",
        "action:load_more",
        "action:create",
        "action:unlink",
    ]
    assert isinstance(items[0], ProjectPickerProjectItem)
    assert items[0].match_kind == ProjectMatchKind.CURRENT_LINK
    assert items[0].label == "currently linked"
    assert items[0].recommended is True
    assert isinstance(items[-2], ProjectPickerCreateItem)
    assert items[-2].project_name == "mistral-vibe"
    assert items[-2].recommended is False
    assert isinstance(items[-3], ProjectPickerLoadMoreItem)
    assert isinstance(items[-1], ProjectPickerUnlinkItem)


def test_create_project_is_recommended_when_no_loaded_project_matches() -> None:
    items = build_project_picker_items(
        context=_context(),
        projects=[
            _project("other", "Internal Tools", "https://github.com/mistralai/tools")
        ],
    )

    assert [item.option_id for item in items] == ["action:create"]
    assert isinstance(items[0], ProjectPickerCreateItem)
    assert items[0].project_name == "mistral-vibe"
    assert items[0].recommended is True


def test_load_more_precedes_create_when_no_match_may_exist_on_later_page() -> None:
    items = build_project_picker_items(
        context=_context(),
        projects=[
            _project("other", "Internal Tools", "https://github.com/mistralai/tools")
        ],
        has_more=True,
    )

    assert [item.option_id for item in items] == ["action:load_more", "action:create"]
    assert isinstance(items[-1], ProjectPickerCreateItem)
    assert items[-1].recommended is False


def test_read_only_projects_are_hidden() -> None:
    items = build_project_picker_items(
        context=_context(),
        projects=[
            _project(
                "read-only",
                "Read Only Mistral Vibe",
                CURRENT_REPO_URL,
                is_read_only=True,
            ),
            _project("other", "Internal Tools", "https://github.com/mistralai/tools"),
        ],
    )

    assert [item.option_id for item in items] == ["action:create"]
    assert isinstance(items[0], ProjectPickerCreateItem)
    assert items[0].recommended is True


def test_search_filters_loaded_projects_and_preserves_create_name() -> None:
    items = build_project_picker_items(
        context=_context(),
        projects=[
            _project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL),
            _project("docs", "Documentation", "https://github.com/mistralai/docs"),
        ],
        query="New Project",
    )

    assert [item.option_id for item in items] == ["action:create"]
    assert isinstance(items[0], ProjectPickerCreateItem)
    assert items[0].project_name == "New Project"
    assert items[0].recommended is True


def test_saved_link_with_changed_remote_is_not_current_link() -> None:
    items = build_project_picker_items(
        context=_context(
            saved_link=_link("mistral-vibe", "https://github.com/mistralai/old")
        ),
        projects=[_project("mistral-vibe", "Mistral Vibe", CURRENT_REPO_URL)],
    )

    assert isinstance(items[0], ProjectPickerProjectItem)
    assert items[0].match_kind == ProjectMatchKind.EXACT_REPO
    assert items[0].label == "exact repo match"
