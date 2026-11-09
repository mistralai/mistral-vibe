"""Tests for the forge sync module (vibe.core.git.forge_sync).

Pure parser fixture tests over recorded gh/glab JSON shapes — no network,
no CLI invocation, no repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from vibe.core.git.forge_sync import (
    ForgeSyncError,
    derive_pr_status,
    parse_github_pull_request,
    parse_glab_mr_view,
    parse_glab_pipeline,
    parse_glab_pipeline_jobs,
)
from vibe.core.session.artifacts import GitLabMergeRequestDetails


def _check_run(
    name: str,
    conclusion: str | None,
    *,
    required: bool = True,
    workflow: str = "CI",
    started_at: str = "2026-10-20T10:00:00Z",
) -> dict[str, object]:
    return {
        "__typename": "CheckRun",
        "name": name,
        "conclusion": conclusion,
        "startedAt": started_at,
        "detailsUrl": f"https://github.com/runs/{name}",
        "isRequired": required,
        "checkSuite": {"workflowRun": {"workflow": {"name": workflow}}},
    }


def _github_page(
    contexts: list[dict[str, object]] | None = None,
    *,
    check_suites: list[dict[str, object]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    """One page of ``gh api graphql --paginate`` for the PR query."""
    pull_request: dict[str, object] = {
        "title": "Fix login bug",
        "state": "OPEN",
        "isDraft": False,
        "headRefName": "fix/login",
        "baseRefName": "main",
        "author": {"login": "pierre"},
        "createdAt": "2026-10-20T10:00:00Z",
        "closedAt": None,
        "mergedAt": None,
        "mergeCommit": None,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "checkSuites": {"nodes": check_suites or []},
                        "statusCheckRollup": {
                            "contexts": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": contexts or [],
                            }
                        },
                    }
                }
            ]
        },
    }
    pull_request.update(overrides)
    return {"data": {"repository": {"pullRequest": pull_request}}}


class TestParseGithubPullRequest:
    def test_open_pr(self):
        snapshot = parse_github_pull_request([_github_page()], checked_at="T1")
        assert snapshot.state == "open"
        assert snapshot.title == "Fix login bug"
        assert snapshot.source_branch == "fix/login"
        assert snapshot.target_branch == "main"
        assert snapshot.author == "pierre"
        assert snapshot.created_at == "2026-10-20T10:00:00Z"
        assert snapshot.review_decision == "APPROVED"
        assert snapshot.merge_status == "mergeable"
        # No check: the provider answered, no check gates the merge.
        assert snapshot.checks is not None
        assert snapshot.checks.state == "none"
        assert snapshot.checks.checked_at == "T1"

    def test_lifecycle(self):
        assert (
            parse_github_pull_request([_github_page(isDraft=True)], "").state == "draft"
        )
        merged = parse_github_pull_request(
            [
                _github_page(
                    state="MERGED",
                    mergedAt="2026-10-21T09:00:00Z",
                    mergeCommit={"oid": "abc123"},
                )
            ],
            "",
        )
        assert merged.state == "merged"
        assert merged.merged_at == "2026-10-21T09:00:00Z"
        assert merged.merge_commit_sha == "abc123"
        assert parse_github_pull_request([_github_page(state="CLOSED")], "").state == (
            "closed"
        )

    @pytest.mark.parametrize(
        ("merge_state", "expected"),
        [
            ("CLEAN", "mergeable"),
            ("HAS_HOOKS", "mergeable"),
            # Only optional checks failed: reported apart, never blocking.
            ("UNSTABLE", "mergeable"),
            ("BLOCKED", "blocked"),
            # Branch protection requires an up-to-date branch: GitHub refuses
            # the merge until it is updated.
            ("BEHIND", "blocked"),
            ("DIRTY", "conflicting"),
            # GitHub is still computing it.
            ("UNKNOWN", None),
        ],
    )
    def test_merge_state(self, merge_state: str, expected: str | None):
        snapshot = parse_github_pull_request(
            [_github_page(mergeStateStatus=merge_state)], ""
        )
        assert snapshot.merge_status == expected

    def test_only_required_checks_gate_the_merge(self):
        snapshot = parse_github_pull_request(
            [
                _github_page([
                    _check_run("test", "SUCCESS"),
                    _check_run("label check", "FAILURE", required=False),
                ])
            ],
            "",
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pass"
        assert [check.name for check in snapshot.checks.checks] == ["CI / test"]
        assert snapshot.optional_checks_state == "fail"

    @pytest.mark.parametrize("conclusion", ["SKIPPED", "NEUTRAL"])
    def test_skipped_and_neutral_checks_pass(self, conclusion: str):
        snapshot = parse_github_pull_request(
            [_github_page([_check_run("build", conclusion)])], ""
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pass"

    def test_a_check_without_conclusion_is_pending(self):
        snapshot = parse_github_pull_request(
            [_github_page([_check_run("build", None)])], ""
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pending"

    def test_the_latest_run_of_a_check_wins(self):
        # A re-run adds a run to the rollup; the cancelled one is superseded.
        snapshot = parse_github_pull_request(
            [
                _github_page([
                    _check_run("build", "CANCELLED", started_at="2026-10-20T10:00:00Z"),
                    _check_run("build", "SUCCESS", started_at="2026-10-20T10:05:00Z"),
                ])
            ],
            "",
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pass"
        assert len(snapshot.checks.checks) == 1

    def test_a_queued_rerun_supersedes_the_finished_run(self):
        # A queued re-run has no start time yet: it is still the latest run.
        rerun = _check_run("build", None)
        rerun["startedAt"] = None
        snapshot = parse_github_pull_request(
            [_github_page([_check_run("build", "FAILURE"), rerun])], ""
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pending"

    def test_checks_of_other_workflows_are_other_checks(self):
        snapshot = parse_github_pull_request(
            [
                _github_page([
                    _check_run("build", "SUCCESS", workflow="Web"),
                    _check_run("build", "FAILURE", workflow="Desktop"),
                ])
            ],
            "",
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"

    def test_a_required_check_of_a_workflow_that_failed_to_start_fails(self):
        snapshot = parse_github_pull_request(
            [
                _github_page(
                    [_check_run("tests", "SKIPPED", workflow="Kazekit")],
                    check_suites=[
                        {
                            "conclusion": "SUCCESS",
                            "workflowRun": {"workflow": {"name": "Kazekit"}},
                        },
                        {
                            "conclusion": "STARTUP_FAILURE",
                            "workflowRun": {"workflow": {"name": "Kazekit"}},
                        },
                    ],
                )
            ],
            "",
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"

    def test_a_restarted_workflow_is_not_a_startup_failure(self):
        snapshot = parse_github_pull_request(
            [
                _github_page(
                    [_check_run("tests", "SKIPPED", workflow="Kazekit")],
                    check_suites=[
                        {
                            "conclusion": "STARTUP_FAILURE",
                            "workflowRun": {"workflow": {"name": "Kazekit"}},
                        },
                        {
                            "conclusion": "SUCCESS",
                            "workflowRun": {"workflow": {"name": "Kazekit"}},
                        },
                    ],
                )
            ],
            "",
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pass"

    def test_status_contexts_count(self):
        snapshot = parse_github_pull_request(
            [
                _github_page([
                    {
                        "__typename": "StatusContext",
                        "context": "buildkite/dashboard-prs",
                        "state": "FAILURE",
                        "targetUrl": "https://buildkite.com/1",
                        "isRequired": True,
                    }
                ])
            ],
            "",
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"
        assert snapshot.checks.checks[0].name == "buildkite/dashboard-prs"

    def test_checks_on_later_pages_count(self):
        first = _github_page([_check_run("lint", "SUCCESS")])
        second = _github_page([_check_run("test", "FAILURE")])
        snapshot = parse_github_pull_request([first, second], "")
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"
        assert len(snapshot.checks.checks) == 2

    def test_malformed_payload_raises(self):
        with pytest.raises(ForgeSyncError):
            parse_github_pull_request([], "")
        with pytest.raises(ForgeSyncError):
            parse_github_pull_request([{"data": {"repository": None}}], "")
        with pytest.raises(ForgeSyncError):
            parse_github_pull_request([_github_page(state="MAYBE")], "")


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

    def test_pipeline_jobs_detail_the_pipeline_status(self):
        pipeline = {"id": 42, "status": "failed"}
        # The listing may miss the failed job (it was not fetched); the
        # pipeline status covers every job.
        jobs = [{"name": "rspec", "status": "success", "web_url": "https://gl/j/1"}]
        snapshot = parse_glab_mr_view(
            self._payload(), pipeline=pipeline, pipeline_jobs=jobs
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"
        assert [c.name for c in snapshot.checks.checks] == ["rspec"]

    def test_jobs_allowed_to_fail_do_not_gate_the_merge(self):
        jobs = [
            {"name": "rspec", "status": "success"},
            {"name": "audit", "status": "failed", "allow_failure": True},
        ]
        snapshot = parse_glab_mr_view(
            self._payload(), pipeline={"status": "success"}, pipeline_jobs=jobs
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pass"
        assert snapshot.optional_checks_state == "fail"

    def test_a_blocking_manual_job_holds_the_checks(self):
        jobs = [
            {"name": "deploy", "status": "manual"},
            {"name": "smoke", "status": "manual", "allow_failure": True},
        ]
        snapshot = parse_glab_mr_view(
            self._payload(), pipeline={"status": "manual"}, pipeline_jobs=jobs
        )
        assert snapshot.checks is not None
        assert snapshot.checks.state == "pending"
        assert [c.status for c in snapshot.checks.checks] == ["pending"]
        # Nobody has to run an optional manual job.
        assert snapshot.optional_checks_state == "pass"

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

    @pytest.mark.parametrize(
        ("detailed_merge_status", "expected"),
        [
            ("mergeable", "mergeable"),
            # GitHub's BEHIND: GitLab refuses the merge until a rebase.
            ("need_rebase", "blocked"),
            ("conflict", "conflicting"),
            ("cannot_be_merged", "conflicting"),
            ("blocked_status", "blocked"),
            # Reported by the checks and review statuses instead.
            ("ci_still_running", None),
            ("not_approved", None),
        ],
    )
    def test_merge_status(self, detailed_merge_status: str, expected: str | None):
        snapshot = parse_glab_mr_view(
            self._payload(detailed_merge_status=detailed_merge_status)
        )
        assert snapshot.merge_status == expected

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

    def test_skipped_pipeline_passes(self):
        summary = parse_glab_pipeline({"status": "skipped"}, checked_at="T")
        assert summary is not None
        assert summary.state == "pass"

    def test_jobs_non_list_is_none(self):
        assert parse_glab_pipeline_jobs({"nope": 1}) is None

    def test_jobs_empty_is_none(self):
        assert parse_glab_pipeline_jobs([]) is None


# Shared with le-chat-web's getPullRequestStatus, whose test reads this file.
_STATUS_CASES = json.loads(
    (Path(__file__).parent / "pull_request_status_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(
    "case", [pytest.param(case, id=case["name"]) for case in _STATUS_CASES]
)
def test_derive_pr_status_matches_the_clients(case: dict[str, Any]):
    status_input = case["input"]
    if "state" not in status_input:
        # Stored artifacts always have a lifecycle state.
        assert case["cliStatus"] is None
        assert case["cliReason"] is None
        return
    assert derive_pr_status(
        status_input["state"],
        status_input.get("checksStatus"),
        status_input.get("reviewDecision"),
        status_input.get("mergeStatus"),
    ) == (case["cliStatus"], case["cliReason"])


def _gh_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    import subprocess

    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _batch_answer(*pull_requests: dict[str, object] | None, errors=None) -> str:
    """A batched GraphQL answer: one alias per PR (``null`` when missing)."""
    data: dict[str, object] = {
        "rateLimit": {
            "remaining": 4200,
            "limit": 5000,
            "resetAt": "2026-10-29T10:00:00Z",
        }
    }
    for index, pull_request in enumerate(pull_requests):
        data[f"p{index}"] = (
            None if pull_request is None else {"pullRequest": pull_request}
        )
    return json.dumps({"data": data, **({"errors": errors} if errors else {})})


def _pull_request(page: dict[str, object]) -> dict[str, Any]:
    return cast(Any, page)["data"]["repository"]["pullRequest"]


class TestBatchedGitHubReads:
    def test_a_batch_reads_each_pull_request_and_the_quota(self, monkeypatch):
        import vibe.core.git.forge_sync as fs

        found = _pull_request(_github_page([_check_run("lint", "FAILURE")]))
        # A missing PR is null in the data and gh exits with an error.
        monkeypatch.setattr(
            fs,
            "_run_process",
            lambda cmd, env_name, host, label: _gh_process(
                _batch_answer(found, None, errors=[{"type": "NOT_FOUND"}]), returncode=1
            ),
        )
        first = fs.GitHubPullRequestRef("o", "r", 1)
        missing = fs.GitHubPullRequestRef("o", "r", 2)

        results, quota = fs.read_github_pull_requests(
            "gh", "github.com", [first, missing], "T1"
        )

        snapshot = results[first]
        assert isinstance(snapshot, fs.PullRequestSnapshot)
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"
        assert isinstance(results[missing], fs.ForgeSyncError)
        assert quota is not None
        assert (quota.remaining, quota.limit) == (4200, 5000)

    def test_checks_beyond_the_first_page_count(self, monkeypatch):
        import vibe.core.git.forge_sync as fs

        first_page = _pull_request(_github_page([_check_run("lint", "SUCCESS")]))
        first_page["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
            "pageInfo"
        ] = {"hasNextPage": True, "endCursor": "MTAw"}
        commands: list[list[str]] = []

        def run(cmd, env_name, host, label):
            commands.append(cmd)
            if "--paginate" in cmd:
                # The rest of the checks, back to back from the cursor.
                return _gh_process(
                    json.dumps(_github_page([_check_run("test", "FAILURE")]))
                )
            return _gh_process(_batch_answer(first_page))

        monkeypatch.setattr(fs, "_run_process", run)
        ref = fs.GitHubPullRequestRef("o", "r", 1)

        results, _quota = fs.read_github_pull_requests("gh", "github.com", [ref], "T")

        snapshot = results[ref]
        assert isinstance(snapshot, fs.PullRequestSnapshot)
        assert snapshot.checks is not None
        assert [c.name for c in snapshot.checks.checks] == ["CI / lint", "CI / test"]
        assert "endCursor=MTAw" in commands[1]

    def test_a_host_without_is_required_counts_every_check(self, monkeypatch):
        import vibe.core.git.forge_sync as fs

        monkeypatch.setattr(fs, "_HOSTS_WITHOUT_IS_REQUIRED", set())
        page = _pull_request(_github_page([_check_run("lint", "FAILURE")]))
        del page["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"][
            "nodes"
        ][0]["isRequired"]
        queries: list[str] = []

        def run(cmd, env_name, host, label):
            query = cmd[-1]
            queries.append(query)
            if "isRequired" in query:
                # An older GitHub Enterprise rejects the whole query.
                return _gh_process(
                    json.dumps({"errors": [{"message": "Field 'isRequired' ..."}]}),
                    returncode=1,
                )
            return _gh_process(_batch_answer(page))

        monkeypatch.setattr(fs, "_run_process", run)
        ref = fs.GitHubPullRequestRef("o", "r", 1)

        results, _quota = fs.read_github_pull_requests("gh", "ghe.corp", [ref], "T")

        snapshot = results[ref]
        assert isinstance(snapshot, fs.PullRequestSnapshot)
        assert snapshot.checks is not None
        assert snapshot.checks.state == "fail"
        assert len(queries) == 2

    def test_the_check_fingerprint_follows_the_checks(self, monkeypatch):
        import vibe.core.git.forge_sync as fs

        answers = iter([
            _batch_answer({"headRefOid": "a", "rollup": "PENDING"}),
            _batch_answer({"headRefOid": "a", "rollup": "PENDING"}),
            _batch_answer({"headRefOid": "a", "rollup": "SUCCESS"}),
        ])
        monkeypatch.setattr(
            fs,
            "_run_process",
            lambda cmd, env_name, host, label: _gh_process(next(answers)),
        )
        ref = fs.GitHubPullRequestRef("o", "r", 1)

        def fingerprint() -> str | None:
            return fs.read_github_checks("gh", "github.com", [ref])[0][ref]

        first = fingerprint()
        assert first is not None
        assert fingerprint() == first
        assert fingerprint() != first

    @pytest.mark.parametrize(
        ("stderr", "reset_at"),
        [
            ("API rate limit exceeded for user", 1_700_000_000.0),
            # Lifts sooner than the quota resets: the caller's delay applies.
            ("You have exceeded a secondary rate limit", None),
        ],
    )
    def test_rate_limit_reset(self, monkeypatch, stderr: str, reset_at: float | None):
        import vibe.core.git.forge_sync as fs

        def run(cmd, env_name, host, label):
            if cmd[1:3] == ["api", "rate_limit"]:
                return _gh_process("1700000000\n")
            return _gh_process(stderr=stderr, returncode=1)

        monkeypatch.setattr(fs, "_run_process", run)
        with pytest.raises(fs.RateLimitedSyncError) as raised:
            fs.read_github_checks(
                "gh", "github.com", [fs.GitHubPullRequestRef("o", "r", 1)]
            )
        assert raised.value.reset_at == reset_at


class TestFetchGitLabMergeRequest:
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
            fs.fetch_gitlab_merge_request(details, checked_at="")
