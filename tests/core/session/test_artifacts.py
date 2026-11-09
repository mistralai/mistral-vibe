"""Tests for session artifact models (vibe.core.session.artifacts)."""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
import pytest

from vibe.core.session.artifacts import (
    GitHubPullRequestArtifact,
    GitHubPullRequestDetails,
    GitLabMergeRequestArtifact,
    GitLabMergeRequestDetails,
    SessionArtifact,
)
from vibe.core.types import SessionMetadata

_adapter = TypeAdapter(list[SessionArtifact])

# Phase 4: the enrichment fields are required keys. Fields that are genuinely
# absent for an open PR stay nullable but present.
_ENRICHMENT_DEFAULTS = {
    "state": "open",
    "source_branch": "fix/login",
    "target_branch": "main",
    "author": "pierre",
    "provider_created_at": "2026-10-13T10:00:00+00:00",
    "provider_updated_at": None,
    "provider_closed_at": None,
    "provider_merged_at": None,
    "merge_commit_sha": None,
    "pr_status": None,
    "checks": None,
}


def _github_details(**overrides) -> GitHubPullRequestDetails:
    defaults = {
        "repo_host": "github.com",
        "repo_owner": "mistralai",
        "repo_name": "vibe",
        "pull_request_number": 123,
        **_ENRICHMENT_DEFAULTS,
    }
    return GitHubPullRequestDetails(**{**defaults, **overrides})


def _gitlab_details(**overrides) -> GitLabMergeRequestDetails:
    defaults = {
        "repo_host": "gitlab.com",
        "repo_id": 12345,
        "project_path": "org/repo",
        "merge_request_iid": 42,
        **_ENRICHMENT_DEFAULTS,
    }
    return GitLabMergeRequestDetails(**{**defaults, **overrides})


def _github_artifact(**overrides) -> GitHubPullRequestArtifact:
    defaults = {
        "id": "artifact-1",
        "session_id": "session-1",
        "external_key": "github.com/mistralai/vibe/pull/123",
        "external_url": "https://github.com/mistralai/vibe/pull/123",
        "display_title": "Fix login bug",
        "discovered_at": "2026-10-13T10:15:00+00:00",
        "details": _github_details(),
    }
    return GitHubPullRequestArtifact(**{**defaults, **overrides})


def _gitlab_artifact(**overrides) -> GitLabMergeRequestArtifact:
    defaults = {
        "id": "artifact-2",
        "session_id": "session-1",
        "external_key": "gitlab.com/12345/merge_requests/42",
        "external_url": "https://gitlab.com/org/repo/-/merge_requests/42",
        "display_title": "Fix bug",
        "discovered_at": "2026-10-13T11:00:00+00:00",
        "details": _gitlab_details(),
    }
    return GitLabMergeRequestArtifact(**{**defaults, **overrides})


class TestGitHubPullRequestDetails:
    def test_required_fields(self):
        details = _github_details()
        assert details.kind == "pull_request"
        assert details.schema_version == 1
        assert details.repo_host == "github.com"
        assert details.repo_owner == "mistralai"
        assert details.repo_name == "vibe"
        assert details.pull_request_number == 123

    def test_enrichment_fields_required_keys(self):
        """Phase 4 strictness: enrichment fields are required keys; the ones
        genuinely absent for an open PR stay nullable but present, and the
        Phase 4 additions default to None.
        """
        details = _github_details()
        assert details.source_branch == "fix/login"
        assert details.target_branch == "main"
        assert details.author == "pierre"
        assert details.state == "open"
        assert details.provider_created_at == "2026-10-13T10:00:00+00:00"
        assert details.provider_updated_at is None
        assert details.provider_closed_at is None
        assert details.provider_merged_at is None
        assert details.merge_commit_sha is None
        assert details.pr_status is None
        assert details.checks is None
        with pytest.raises(ValidationError):
            # Static-safe construction: omitting the now-required enrichment
            # keys must still be rejected by validation.
            GitHubPullRequestDetails.model_validate({
                "repo_host": "github.com",
                "repo_owner": "mistralai",
                "repo_name": "vibe",
                "pull_request_number": 123,
                "state": "open",
                "source_branch": "fix/login",
                "target_branch": "main",
                "author": "pierre",
            })

    def test_enrichment_fields_populated(self):
        details = _github_details(
            source_branch="fix/login",
            target_branch="main",
            author="pierre",
            state="open",
            provider_created_at="2026-10-13T10:00:00+00:00",
        )
        assert details.source_branch == "fix/login"
        assert details.state == "open"


class TestGitLabMergeRequestDetails:
    def test_required_fields(self):
        details = _gitlab_details()
        assert details.kind == "pull_request"
        assert details.schema_version == 1
        assert details.repo_host == "gitlab.com"
        assert details.repo_id == 12345
        assert details.project_path == "org/repo"
        assert details.merge_request_iid == 42

    def test_repo_id_optional_fallback(self):
        """repo_id is optional — fallback when glab is unavailable."""
        details = _gitlab_details(repo_id=None)
        assert details.repo_id is None
        assert details.project_path == "org/repo"


class TestSessionArtifactSerialization:
    def test_github_artifact_roundtrip(self):
        artifact = _github_artifact()
        dumped = artifact.model_dump(mode="json")
        assert dumped["provider"] == "github"
        assert dumped["kind"] == "pull_request"
        assert dumped["external_key"] == "github.com/mistralai/vibe/pull/123"
        assert dumped["viewed_at"] is None
        restored = GitHubPullRequestArtifact.model_validate(dumped)
        assert restored == artifact

    def test_gitlab_artifact_roundtrip(self):
        artifact = _gitlab_artifact()
        dumped = artifact.model_dump(mode="json")
        assert dumped["provider"] == "gitlab"
        restored = GitLabMergeRequestArtifact.model_validate(dumped)
        assert restored == artifact

    def test_viewed_at_defaults_none(self):
        artifact = _github_artifact()
        assert artifact.viewed_at is None

    def test_display_title_nullable(self):
        artifact = _github_artifact(display_title=None)
        assert artifact.display_title is None


class TestDiscriminatedUnion:
    """SessionArtifact = Annotated[GitHub | GitLab, Field(discriminator='provider')]."""

    def test_parse_github(self):
        data = _github_artifact().model_dump(mode="json")
        artifacts = _adapter.validate_python([data])
        assert len(artifacts) == 1
        assert isinstance(artifacts[0], GitHubPullRequestArtifact)
        assert artifacts[0].provider == "github"

    def test_parse_gitlab(self):
        data = _gitlab_artifact().model_dump(mode="json")
        artifacts = _adapter.validate_python([data])
        assert len(artifacts) == 1
        assert isinstance(artifacts[0], GitLabMergeRequestArtifact)
        assert artifacts[0].provider == "gitlab"

    def test_parse_mixed_list(self):
        data = [
            _github_artifact().model_dump(mode="json"),
            _gitlab_artifact().model_dump(mode="json"),
        ]
        artifacts = _adapter.validate_python(data)
        assert len(artifacts) == 2
        assert isinstance(artifacts[0], GitHubPullRequestArtifact)
        assert isinstance(artifacts[1], GitLabMergeRequestArtifact)

    def test_roundtrip_preserves_types(self):
        artifacts = [_github_artifact(), _gitlab_artifact()]
        dumped = _adapter.dump_python(artifacts, mode="json")
        restored = _adapter.validate_python(dumped)
        assert restored == artifacts

    def test_gitlab_without_repo_id(self):
        """Fallback case: artifact created without repo_id resolution."""
        artifact = _gitlab_artifact(
            external_key="gitlab.com/org/repo/-/merge_requests/42",
            details=_gitlab_details(repo_id=None),
        )
        dumped = artifact.model_dump(mode="json")
        restored = _adapter.validate_python([dumped])
        assert isinstance(restored[0], GitLabMergeRequestArtifact)
        assert restored[0].details is not None
        assert restored[0].details.repo_id is None


class TestSessionMetadataArtifacts:
    def test_artifacts_default_empty(self):
        meta = SessionMetadata(
            session_id="s1",
            start_time="2026-01-01T00:00:00+00:00",
            end_time=None,
            git_commit=None,
            git_branch=None,
            environment={},
            username="test",
        )
        assert meta.artifacts == []

    def test_artifacts_with_values(self):
        meta = SessionMetadata(
            session_id="s1",
            start_time="2026-01-01T00:00:00+00:00",
            end_time=None,
            git_commit=None,
            git_branch=None,
            environment={},
            username="test",
            artifacts=[_github_artifact(), _gitlab_artifact()],
        )
        assert len(meta.artifacts) == 2
        dumped = meta.model_dump(mode="json")
        restored = SessionMetadata.model_validate(dumped)
        assert len(restored.artifacts) == 2

    def test_retrocompatibility_no_artifacts_field(self):
        """Old meta.json without the artifacts field loads with empty list."""
        old_json = (
            '{"session_id": "s1",'
            ' "start_time": "2026-01-01T00:00:00+00:00",'
            ' "end_time": null,'
            ' "git_commit": null,'
            ' "git_branch": null,'
            ' "environment": {},'
            ' "username": "test"}'
        )
        meta = SessionMetadata.model_validate_json(old_json)
        assert meta.artifacts == []
