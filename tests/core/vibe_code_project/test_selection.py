from __future__ import annotations

from vibe.utils.repository import normalize_repo_url, repo_url_label

CURRENT_REPO_URL = "https://github.com/mistralai/mistral-vibe.git"


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
