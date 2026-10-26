"""Tests for the forge sync module (vibe.core.git.forge_sync).

Pure parser fixture tests over recorded gh/glab JSON shapes — no network,
no CLI invocation, no repository.
"""

from __future__ import annotations

import json
from typing import Literal

import pytest

from vibe.core.git.forge_sync import (
    ForgeSyncError,
    derive_pr_status,
    parse_github_check_rollup,
    parse_github_pr_view,
    parse_glab_mr_view,
    parse_glab_pipeline,
    parse_glab_pipeline_jobs,
)
from vibe.core.session.artifacts import (
    ChecksSummary,
    GitHubPullRequestDetails,
    GitLabMergeRequestDetails,
)


def _github_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "number": 123,
        "title": "Fix login bug",
        "state": "OPEN",
        "isDraft": False,
        "url": "https://github.com/mistralai/vibe/pull/123",
        "headRefName": "fix/login",
        "baseRefName": "main",
        "author": {"login": "pierre"},
        "createdAt": "2026-10-20T10:00:00Z",
        "updatedAt": "2026-10-20T12:00:00Z",
        "closedAt": None,
        "mergedAt": None,
        "mergeCommit": None,
        "mergeable": "MERGEABLE",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [],
    }
    payload.update(overrides)
    return payload


class TestParseGithubPrView:
    def test_open_pr(self):
        snapshot = parse_github_pr_view(_github_payload())
        assert snapshot.state == "open"
        assert snapshot.title == "Fix login bug"
        assert snapshot.source_branch == "fix/login"
        assert snapshot.target_branch == "main"
        assert snapshot.author == "pierre"
        assert snapshot.created_at == "2026-10-20T10:00:00Z"
        assert snapshot.merge_commit_sha is None
        assert snapshot.review_decision == "APPROVED"
        assert snapshot.mergeable is True
        # Empty rollup: the provider answered, there are no checks.
        assert snapshot.checks is not None
        assert snapshot.checks.state == "none"

    def test_draft_pr(self):
        snapshot = parse_github_pr_view(_github_payload(isDraft=True))
        assert snapshot.state == "draft"

    def test_merged_pr(self):
        snapshot = parse_github_pr_view(
            _github_payload(
                state="MERGED",
                mergedAt="2026-10-21T09:00:00Z",
                mergeCommit={"oid": "abc123"},
                closedAt="2026-10-21T09:00:00Z",
            )
        )
        assert snapshot.state == "merged"
        assert snapshot.merged_at == "2026-10-21T09:00:00Z"
        assert snapshot.merge_commit_sha == "abc123"

    def test_closed_pr(self):
        snapshot = parse_github_pr_view(
            _github_payload(state="CLOSED", closedAt="2026-10-21T08:00:00Z")
        )
        assert snapshot.state == "closed"
        assert snapshot.closed_at == "2026-10-21T08:00:00Z"

    def test_mergeable_unknown(self):
        snapshot = parse_github_pr_view(_github_payload(mergeable="UNKNOWN"))
        assert snapshot.mergeable is None

    def test_missing_review_decision(self):
        snapshot = parse_github_pr_view(_github_payload(reviewDecision=None))
        assert snapshot.review_decision is None

    def test_malformed_payload_raises(self):
        with pytest.raises(ForgeSyncError):
            parse_github_pr_view(["not", "an", "object"])
        with pytest.raises(ForgeSyncError):
            parse_github_pr_view({"title": "no state"})
        with pytest.raises(ForgeSyncError):
            parse_github_pr_view(_github_payload(state="MAYBE"))


class TestParseGithubCheckRollup:
    def test_check_run_pass(self):
        rollup = [
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/1",
            }
        ]
        summary = parse_github_check_rollup(rollup, checked_at="T1")
        assert summary is not None
        assert summary.state == "pass"
        assert summary.checked_at == "T1"
        assert summary.checks[0].name == "ci / test"
        assert summary.checks[0].status == "pass"
        assert summary.checks[0].url == "https://github.com/1"

    def test_check_run_failure_wins(self):
        rollup = [
            {
                "__typename": "CheckRun",
                "name": "ok",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            },
            {
                "__typename": "CheckRun",
                "name": "bad",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            },
        ]
        summary = parse_github_check_rollup(rollup, checked_at="")
        assert summary is not None
        assert summary.state == "fail"

    def test_pending_check(self):
        rollup = [
            {
                "__typename": "CheckRun",
                "name": "running",
                "status": "IN_PROGRESS",
                "conclusion": None,
            }
        ]
        summary = parse_github_check_rollup(rollup, checked_at="")
        assert summary is not None
        assert summary.state == "pending"
        assert summary.checks[0].status == "pending"

    def test_status_context(self):
        rollup = [
            {
                "__typename": "StatusContext",
                "context": "ci/travis",
                "state": "FAILURE",
                "targetUrl": "https://ci.example.com/1",
            }
        ]
        summary = parse_github_check_rollup(rollup, checked_at="")
        assert summary is not None
        assert summary.state == "fail"
        assert summary.checks[0].name == "ci/travis"

    def test_non_list_is_no_data(self):
        assert parse_github_check_rollup(None, checked_at="") is None
        assert parse_github_check_rollup({"unexpected": True}, checked_at="") is None


class TestParseGlabMrView:
    def _payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "iid": 7,
            "title": "Add feature",
            "state": "opened",
            "draft": False,
            "web_url": "https://gitlab.com/team/project/-/merge_requests/7",
            "source_branch": "feat/x",
            "target_branch": "main",
            "author": {"username": "pierre"},
            "created_at": "2026-10-20T10:00:00Z",
            "updated_at": "2026-10-20T12:00:00Z",
            "closed_at": None,
            "merged_at": None,
            "merge_commit_sha": None,
            "detailed_merge_status": "mergeable",
            "merge_status": "can_be_merged",
        }
        payload.update(overrides)
        return payload

    def test_open_mr(self):
        snapshot = parse_glab_mr_view(self._payload())
        assert snapshot.state == "open"
        assert snapshot.title == "Add feature"
        assert snapshot.source_branch == "feat/x"
        assert snapshot.author == "pierre"

    def test_draft_mr(self):
        snapshot = parse_glab_mr_view(self._payload(draft=True))
        assert snapshot.state == "draft"

    def test_merged_and_closed(self):
        assert parse_glab_mr_view(self._payload(state="merged")).state == "merged"
        assert parse_glab_mr_view(self._payload(state="closed")).state == "closed"

    def test_pipeline_summary(self):
        pipeline = {
            "id": 42,
            "iid": 9,
            "status": "success",
            "web_url": "https://gl/p/9",
        }
        snapshot = parse_glab_mr_view(self._payload(), pipeline=pipeline)
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pass"

    def test_pipeline_jobs_override_pipeline_summary(self):
        pipeline = {"id": 42, "status": "success"}
        jobs = [
            {"name": "rspec", "status": "success", "web_url": "https://gl/j/1"},
            {"name": "lint", "status": "failed", "web_url": "https://gl/j/2"},
        ]
        snapshot = parse_glab_mr_view(
            self._payload(), pipeline=pipeline, pipeline_jobs=jobs
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"
        assert [c.name for c in snapshot.checks.checks] == ["rspec", "lint"]

    def test_no_pipeline_is_no_data(self):
        snapshot = parse_glab_mr_view(self._payload(), pipeline=None)
        assert snapshot.checks is None

    def test_approvals_approved(self):
        snapshot = parse_glab_mr_view(
            self._payload(), approvals={"approved": True, "approved_by": [{"user": {}}]}
        )
        assert snapshot.review_decision == "APPROVED"

    def test_approvals_required(self):
        snapshot = parse_glab_mr_view(
            self._payload(), approvals={"approved": False, "approvals_required": 2}
        )
        assert snapshot.review_decision == "REVIEW_REQUIRED"

    def test_unresolved_discussions_map_to_changes_requested(self):
        snapshot = parse_glab_mr_view(
            self._payload(detailed_merge_status="discussions_not_resolved")
        )
        assert snapshot.review_decision == "CHANGES_REQUESTED"

    def test_conflicts_are_not_mergeable(self):
        snapshot = parse_glab_mr_view(
            self._payload(detailed_merge_status="cannot_be_merged")
        )
        assert snapshot.mergeable is False

    def test_ci_still_running_is_not_a_conflict(self):
        snapshot = parse_glab_mr_view(
            self._payload(detailed_merge_status="ci_still_running")
        )
        assert snapshot.mergeable is None

    def test_malformed_payload_raises(self):
        with pytest.raises(ForgeSyncError):
            parse_glab_mr_view("nope")
        with pytest.raises(ForgeSyncError):
            parse_glab_mr_view({"title": "no state"})


class TestParseGlabPipelineHelpers:
    def test_pipeline_none_is_none(self):
        assert parse_glab_pipeline(None, checked_at="") is None

    def test_failed_pipeline(self):
        summary = parse_glab_pipeline({"status": "failed"}, checked_at="T")
        assert summary is not None
        assert summary.state == "fail"

    def test_jobs_non_list_is_none(self):
        assert parse_glab_pipeline_jobs({"nope": 1}, checked_at="") is None

    def test_jobs_empty_is_none(self):
        assert parse_glab_pipeline_jobs([], checked_at="") is None


class _SnapshotFactory:
    """Build snapshots for the prStatus derivation matrix."""

    @staticmethod
    def make(
        *,
        state: Literal["draft", "open", "merged", "closed"] = "open",
        checks_state: Literal["pass", "fail", "pending", "none"] | None = None,
        review_decision: Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"]
        | None = None,
        mergeable: bool | None = None,
    ):
        from vibe.core.git.forge_sync import PullRequestSnapshot

        checks = (
            ChecksSummary(state=checks_state, checked_at="", checks=[])
            if checks_state is not None
            else None
        )
        return PullRequestSnapshot(
            state=state,
            title="t",
            source_branch="a",
            target_branch="b",
            author="u",
            checks=checks,
            review_decision=review_decision,
            mergeable=mergeable,
        )


class TestDerivePrStatus:
    """The PRD's derivation table, row by row."""

    def test_terminal_states_are_none(self):
        for state in ("draft", "merged", "closed"):
            assert derive_pr_status(_SnapshotFactory.make(state=state)) is None

    def test_changes_requested_wins_over_everything(self):
        snapshot = _SnapshotFactory.make(
            checks_state="pass", review_decision="CHANGES_REQUESTED", mergeable=True
        )
        assert derive_pr_status(snapshot) == "action_needed"

    def test_failed_checks_are_action_needed(self):
        snapshot = _SnapshotFactory.make(
            checks_state="fail", review_decision="APPROVED", mergeable=True
        )
        assert derive_pr_status(snapshot) == "action_needed"

    def test_conflicts_are_action_needed(self):
        snapshot = _SnapshotFactory.make(
            checks_state="pass", review_decision="APPROVED", mergeable=False
        )
        assert derive_pr_status(snapshot) == "action_needed"

    def test_pending_checks_are_waiting(self):
        snapshot = _SnapshotFactory.make(
            checks_state="pending", review_decision="APPROVED", mergeable=True
        )
        assert derive_pr_status(snapshot) == "waiting"

    def test_review_required_is_waiting(self):
        snapshot = _SnapshotFactory.make(
            checks_state="pass", review_decision="REVIEW_REQUIRED", mergeable=True
        )
        assert derive_pr_status(snapshot) == "waiting"

    def test_approved_and_green_is_mergeable(self):
        snapshot = _SnapshotFactory.make(
            checks_state="pass", review_decision="APPROVED", mergeable=True
        )
        assert derive_pr_status(snapshot) == "mergeable"

    def test_no_reviews_no_checks_is_mergeable(self):
        snapshot = _SnapshotFactory.make(
            checks_state="none", review_decision=None, mergeable=True
        )
        assert derive_pr_status(snapshot) == "mergeable"

    def test_no_ci_data_falls_back_to_review_and_mergeability(self):
        # checks is None ("no CI data"): the review decision and mergeability
        # decide, per the PRD.
        snapshot = _SnapshotFactory.make(
            checks_state=None, review_decision="APPROVED", mergeable=True
        )
        assert derive_pr_status(snapshot) == "mergeable"
        snapshot = _SnapshotFactory.make(
            checks_state=None, review_decision="REVIEW_REQUIRED", mergeable=True
        )
        assert derive_pr_status(snapshot) == "waiting"

    def test_unknown_mergeability_does_not_block_mergeable(self):
        snapshot = _SnapshotFactory.make(
            checks_state="pass", review_decision="APPROVED", mergeable=None
        )
        assert derive_pr_status(snapshot) == "mergeable"

    def test_fallback_is_waiting(self):
        # An unmapped combination (no review decision, pending checks handled
        # above) lands on waiting rather than mergeable.
        snapshot = _SnapshotFactory.make(
            checks_state="pending", review_decision="CHANGES_REQUESTED"
        )
        assert derive_pr_status(snapshot) == "action_needed"
        snapshot = _SnapshotFactory.make(
            checks_state="pending", review_decision=None, mergeable=None
        )
        assert derive_pr_status(snapshot) == "waiting"


class TestFetchSnapshotDispatch:
    def test_github_details_dispatch(self, monkeypatch):
        """The dispatcher resolves the CLI binary and calls the GitHub runner."""
        import vibe.core.git.forge_sync as fs

        captured = {}

        def fake_run(cmd, env_name, host, label):
            captured["cmd"] = cmd
            captured["env_name"] = env_name
            return json.dumps(_github_payload())

        monkeypatch.setattr(fs, "_run_command", fake_run)
        monkeypatch.setattr(fs, "find_cli_binary", lambda name: f"/usr/bin/{name}")
        details = GitHubPullRequestDetails(
            repo_host="github.com",
            repo_owner="mistralai",
            repo_name="vibe",
            pull_request_number=123,
            state="open",
            source_branch="fix/login",
            target_branch="main",
            author="pierre",
            provider_created_at=None,
            provider_updated_at=None,
            provider_closed_at=None,
            provider_merged_at=None,
            merge_commit_sha=None,
            pr_status=None,
            checks=None,
        )
        snapshot = fs.fetch_pull_request_snapshot(details, checked_at="T1")
        assert snapshot.title == "Fix login bug"
        assert captured["env_name"] == "GH_HOST"
        assert snapshot.checks is not None
        assert snapshot.checks.checked_at == "T1"

    def test_missing_cli_raises(self, monkeypatch):
        import vibe.core.git.forge_sync as fs

        monkeypatch.setattr(fs, "find_cli_binary", lambda name: None)
        details = GitHubPullRequestDetails(
            repo_host="github.com",
            repo_owner="mistralai",
            repo_name="vibe",
            pull_request_number=123,
            state="open",
            source_branch="fix/login",
            target_branch="main",
            author="pierre",
            provider_created_at=None,
            provider_updated_at=None,
            provider_closed_at=None,
            provider_merged_at=None,
            merge_commit_sha=None,
            pr_status=None,
            checks=None,
        )
        with pytest.raises(ForgeSyncError):
            fs.fetch_pull_request_snapshot(details, checked_at="")

    def test_gitlab_missing_cli_raises(self, monkeypatch):
        import vibe.core.git.forge_sync as fs

        monkeypatch.setattr(fs, "find_cli_binary", lambda name: None)
        details = GitLabMergeRequestDetails(
            repo_host="gitlab.com",
            repo_id=None,
            project_path="team/project",
            merge_request_iid=7,
            state="open",
            source_branch="feat/x",
            target_branch="main",
            author="pierre",
            provider_created_at=None,
            provider_updated_at=None,
            provider_closed_at=None,
            provider_merged_at=None,
            merge_commit_sha=None,
            pr_status=None,
            checks=None,
        )
        with pytest.raises(ForgeSyncError):
            fs.fetch_pull_request_snapshot(details, checked_at="")
