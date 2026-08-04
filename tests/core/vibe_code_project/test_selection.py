from __future__ import annotations

from pathlib import Path

from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    VibeCodeProjectLink,
    suggested_project_name,
)
from vibe.utils.repository import normalize_repo_url, repo_url_label

CURRENT_REPO_URL = "https://github.com/mistralai/mistral-vibe.git"


def _context(saved_link: VibeCodeProjectLink | None = None) -> ProjectPickerContext:
    return ProjectPickerContext(
        repo_root=Path("/repo/mistral-vibe"),
        repo_url=CURRENT_REPO_URL,
        repo_name="mistral-vibe",
        saved_link=saved_link,
    )


def _link(project_id: str, repo_url: str = CURRENT_REPO_URL) -> VibeCodeProjectLink:
    return VibeCodeProjectLink(
        repo_root=Path("/repo/mistral-vibe"),
        repo_url=repo_url,
        project_id=project_id,
        project_name="Mistral Vibe",
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
