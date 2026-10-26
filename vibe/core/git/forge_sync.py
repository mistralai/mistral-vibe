"""Forge sync — refresh a PR/MR artifact from its provider (Phase 4).

Phase 4 of session artifacts: for each tracked PR/MR artifact, this module
runs ``gh pr view --json`` / ``glab mr view --json`` (plus the provider's
check-run or pipeline query) and returns a :class:`PullRequestSnapshot` —
the lifecycle state, title, branches, author, provider timestamps, merge
commit SHA, the CI checks summary, and the sync-time inputs (review
decision, mergeability) that feed the aggregated ``prStatus``.

The parsers are pure: functions from CLI JSON output to snapshots, with no
session, no persistence, and no I/O. The thin command runners at the bottom
mirror ``forge.py``'s shape (pinned host env, UTF-8 decode, timeout guard)
and are called in a thread by the artifact service.

Local only. No cloud-sandbox support in Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
import subprocess
from typing import Literal

from vibe.core.git.forge import ForgeError
from vibe.core.session.artifact_detector import find_cli_binary
from vibe.core.session.artifacts import (
    ChecksSummary,
    CheckStatus,
    GitHubPullRequestDetails,
    GitLabMergeRequestDetails,
    PrStatus,
)
from vibe.observability.logging import logger

__all__ = [
    "ForgeSyncError",
    "PullRequestSnapshot",
    "RateLimitedSyncError",
    "derive_pr_status",
    "fetch_pull_request_snapshot",
    "parse_github_check_rollup",
    "parse_github_pr_view",
    "parse_glab_mr_view",
    "parse_glab_pipeline",
    "parse_glab_pipeline_jobs",
]

# Generous enough that a slow forge answer is not mistaken for a hang; the
# caller treats a timeout as a failed sync and keeps the stored state.
_FETCH_TIMEOUT_SECONDS = 10

_GH_VIEW_FIELDS = (
    "number,title,state,isDraft,url,headRefName,baseRefName,author,"
    "createdAt,updatedAt,closedAt,mergedAt,mergeCommit,mergeable,"
    "reviewDecision,statusCheckRollup,mergeStateStatus"
)


class ForgeSyncError(ForgeError):
    """A provider sync fetch failed (no CLI, no auth, command error, bad JSON)."""


class RateLimitedSyncError(ForgeSyncError):
    """The provider rate-limited the request — the caller backs off."""


@dataclass(frozen=True)
class PullRequestSnapshot:
    """The provider's current truth about one PR/MR.

    ``checks`` is ``None`` when there is no CI data (the checks query failed
    or the provider offers none); ``ChecksSummary.state == "none"`` means the
    provider answered and there are no checks. ``review_decision`` and
    ``mergeable`` are sync-time inputs to :func:`derive_pr_status` — they are
    never stored.
    """

    state: Literal["draft", "open", "merged", "closed"]
    title: str
    source_branch: str
    target_branch: str
    author: str
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    merged_at: str | None = None
    merge_commit_sha: str | None = None
    checks: ChecksSummary | None = None
    review_decision: (
        Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"] | None
    ) = None
    mergeable: bool | None = None
    merge_status: str | None = None


# --- prStatus derivation (the product decision lives here, once) ---


def derive_pr_status(snapshot: PullRequestSnapshot) -> PrStatus | None:  # noqa: PLR0911 - one early return per PRD table row
    """Derive the aggregated actionability status for a PR/MR snapshot.

    Open PRs only — draft, merged and closed return ``None`` (the lifecycle
    ``state`` already says which). Evaluated top-down per the PRD's table;
    ``checks is None`` is treated like "no CI data" (the review decision and
    mergeability decide). Flag meanings: "CI not done" means CI *failed*;
    "approval required" means *waiting on reviewers*; merge conflicts block
    the merge, so they are an action for the user.
    """
    if snapshot.state != "open":
        return None
    checks_state = snapshot.checks.state if snapshot.checks is not None else None

    if snapshot.review_decision == "CHANGES_REQUESTED":
        return "action_needed"
    if checks_state == "fail":
        return "action_needed"
    if snapshot.mergeable is False:
        return "action_needed"
    if checks_state == "pending":
        return "waiting"
    if snapshot.review_decision == "REVIEW_REQUIRED":
        return "waiting"
    if checks_state in {"pass", "none", None}:
        # mergeable is True or unknown (None) — an unknown mergeability must
        # not block the mergeable status.
        return "mergeable"
    return "waiting"


# --- GitHub parsers (pure) ---


def _gh_state(
    state: object, is_draft: object
) -> Literal["draft", "open", "merged", "closed"] | None:
    if state == "MERGED":
        return "merged"
    if state == "CLOSED":
        return "closed"
    if state == "OPEN":
        return "draft" if is_draft is True else "open"
    return None


def _gh_check_run_outcome(conclusion: object) -> Literal["pass", "fail", "pending"]:
    if conclusion == "SUCCESS":
        return "pass"
    if conclusion in {
        "FAILURE",
        "TIMED_OUT",
        "CANCELLED",
        "ACTION_REQUIRED",
        "STARTUP_FAILURE",
    }:
        return "fail"
    return "pending"


def _gh_status_context_outcome(state: object) -> Literal["pass", "fail", "pending"]:
    if state == "SUCCESS":
        return "pass"
    if state in {"FAILURE", "ERROR"}:
        return "fail"
    return "pending"


def _gh_merge_status(merge_state_status: object) -> str | None:
    """Map ``gh pr view``'s ``mergeStateStatus`` to the provider-neutral value.

    UNKNOWN (or an unexpected value) maps to ``None`` — "not known", so the
    artifact keeps whatever was previously stored.
    """
    mapping: dict[str, str] = {
        "CLEAN": "mergeable",
        "HAS_HOOKS": "mergeable",
        "BLOCKED": "blocked",
        "BEHIND": "blocked",
        "UNSTABLE": "blocked",
        "DIRTY": "conflicting",
    }
    return mapping.get(merge_state_status)  # type: ignore[return-value]


def parse_github_check_rollup(rollup: object, checked_at: str) -> ChecksSummary | None:
    """Parse ``gh pr view``'s ``statusCheckRollup`` into a checks summary.

    Returns ``None`` when the rollup is not a list (missing field, GraphQL
    shape change) — "no CI data", not "no checks". An empty list means the
    provider answered and there are no checks (``state: "none"``).
    """
    if not isinstance(rollup, list):
        return None
    entries: list[tuple[str, Literal["pass", "fail", "pending"], str]] = []
    for item in rollup:
        if not isinstance(item, dict):
            continue
        kind = item.get("__typename")
        if kind == "CheckRun":
            name = item.get("name")
            if not isinstance(name, str):
                continue
            outcome = _gh_check_run_outcome(item.get("conclusion"))
            url = item.get("detailsUrl")
            entries.append((name, outcome, url if isinstance(url, str) else ""))
        elif kind == "StatusContext":
            name = item.get("context")
            if not isinstance(name, str):
                continue
            outcome = _gh_status_context_outcome(item.get("state"))
            url = item.get("targetUrl")
            entries.append((name, outcome, url if isinstance(url, str) else ""))
    if not entries:
        return ChecksSummary(state="none", checked_at=checked_at, checks=[])
    outcomes = {outcome for _, outcome, _ in entries}
    if "fail" in outcomes:
        state: Literal["pass", "fail", "pending"] = "fail"
    elif "pending" in outcomes:
        state = "pending"
    else:
        state = "pass"
    return ChecksSummary(
        state=state,
        checked_at=checked_at,
        checks=[
            CheckStatus(name=name, status=outcome, url=url)
            for name, outcome, url in entries
        ],
    )


def parse_github_pr_view(payload: object) -> PullRequestSnapshot:
    """Parse a ``gh pr view --json <fields>`` payload into a snapshot.

    Raises :class:`ForgeSyncError` on a malformed payload — the caller keeps
    the stored state and logs the failure.
    """
    if not isinstance(payload, dict):
        raise ForgeSyncError("gh pr view returned a non-object payload")
    state = _gh_state(payload.get("state"), payload.get("isDraft"))
    title = payload.get("title")
    if state is None or not isinstance(title, str):
        raise ForgeSyncError(
            f"gh pr view payload missing state/title: keys={sorted(payload)}"
        )
    head = payload.get("headRefName")
    base = payload.get("baseRefName")
    author_obj = payload.get("author")
    author = author_obj.get("login", "") if isinstance(author_obj, dict) else ""

    merge_commit = payload.get("mergeCommit")
    merge_commit_sha = (
        merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    )
    if not isinstance(merge_commit_sha, str):
        merge_commit_sha = None

    checks = parse_github_check_rollup(payload.get("statusCheckRollup"), checked_at="")

    mergeable_raw = payload.get("mergeable")
    mergeable: bool | None
    if mergeable_raw == "MERGEABLE":
        mergeable = True
    elif mergeable_raw == "CONFLICTING":
        mergeable = False
    else:
        mergeable = None  # UNKNOWN / unexpected -> None

    review_decision = payload.get("reviewDecision")
    if review_decision not in {"APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        review_decision = None

    merge_status = _gh_merge_status(payload.get("mergeStateStatus"))

    def _opt_str(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    return PullRequestSnapshot(
        state=state,
        title=title,
        source_branch=head if isinstance(head, str) else "",
        target_branch=base if isinstance(base, str) else "",
        author=author,
        created_at=_opt_str("createdAt"),
        updated_at=_opt_str("updatedAt"),
        closed_at=_opt_str("closedAt"),
        merged_at=_opt_str("mergedAt"),
        merge_commit_sha=merge_commit_sha,
        checks=checks,
        review_decision=review_decision,
        mergeable=mergeable,
        merge_status=merge_status,
    )


# --- GitLab parsers (pure) ---


_GLAB_STATE_MAP: dict[str, Literal["draft", "open", "merged", "closed"]] = {
    "opened": "open",
    "merged": "merged",
    "closed": "closed",
    "locked": "open",
}


def parse_glab_pipeline(pipeline: object, checked_at: str) -> ChecksSummary | None:
    """Parse a GitLab head-pipeline object into a checks summary.

    A pipeline-level summary only (one entry named after the pipeline); the
    per-job detail, when fetched, is merged in by :func:`parse_glab_pipeline_jobs`.
    Returns ``None`` when there is no pipeline object — "no CI data".
    """
    if not isinstance(pipeline, dict):
        return None
    status = pipeline.get("status")
    state: Literal["pass", "fail", "pending"]
    if status == "success":
        state = "pass"
    elif status in {"failed", "canceled", "skipped"}:
        state = "fail"
    else:
        state = "pending"  # running/pending/created/manual/... -> pending
    web_url = pipeline.get("web_url")
    identifier = pipeline.get("iid") or pipeline.get("id") or "?"
    return ChecksSummary(
        state=state,
        checked_at=checked_at,
        checks=[
            CheckStatus(
                name=f"pipeline #{identifier}",
                status=state,
                url=web_url if isinstance(web_url, str) else "",
            )
        ],
    )


def parse_glab_pipeline_jobs(jobs: object, checked_at: str) -> ChecksSummary | None:
    """Parse a GitLab pipeline jobs listing into a checks summary.

    Returns ``None`` when the listing is unusable — the caller keeps the
    pipeline-level summary (or no summary at all).
    """
    if not isinstance(jobs, list):
        return None
    entries: list[tuple[str, Literal["pass", "fail", "pending"], str]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = job.get("name")
        if not isinstance(name, str):
            continue
        status = job.get("status")
        outcome: Literal["pass", "fail", "pending"]
        if status == "success":
            outcome = "pass"
        elif status in {"failed", "canceled"}:
            outcome = "fail"
        else:
            outcome = "pending"
        web_url = job.get("web_url")
        entries.append((name, outcome, web_url if isinstance(web_url, str) else ""))
    if not entries:
        return None
    outcomes = {outcome for _, outcome, _ in entries}
    state: Literal["pass", "fail", "pending"]
    if "fail" in outcomes:
        state = "fail"
    elif "pending" in outcomes:
        state = "pending"
    else:
        state = "pass"
    return ChecksSummary(
        state=state,
        checked_at=checked_at,
        checks=[
            CheckStatus(name=name, status=outcome, url=url)
            for name, outcome, url in entries
        ],
    )


def _glab_review_decision(
    detailed_merge_status: object, approvals: object
) -> Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"] | None:
    """Map GitLab's merge/approval state onto the review-decision vocabulary.

    GitLab has no reviewDecision; the PRD settles the mapping: unresolved
    discussions map to "changes requested", approvals (required or given)
    map to the approval states. Anything unmapped stays ``None``.
    """
    if detailed_merge_status == "discussions_not_resolved":
        return "CHANGES_REQUESTED"
    if isinstance(approvals, dict):
        approved_by = approvals.get("approved_by")
        if isinstance(approved_by, list) and approved_by:
            return "APPROVED"
        approvals_required = approvals.get("approvals_required")
        if isinstance(approvals_required, int) and approvals_required > 0:
            return "REVIEW_REQUIRED"
        if approvals.get("approved") is False:
            return "REVIEW_REQUIRED"
    return None


def parse_glab_mr_view(
    payload: object,
    *,
    pipeline: object = None,
    pipeline_jobs: object = None,
    approvals: object = None,
    checked_at: str = "",
) -> PullRequestSnapshot:
    """Parse a ``glab mr view --output json`` payload into a snapshot.

    ``pipeline`` / ``pipeline_jobs`` / ``approvals`` are the optional side
    queries (head pipeline, its jobs, the approvals endpoint); each is
    best-effort and absent means "no data".

    Raises :class:`ForgeSyncError` on a malformed payload.
    """
    if not isinstance(payload, dict):
        raise ForgeSyncError("glab mr view returned a non-object payload")
    state = _GLAB_STATE_MAP.get(payload.get("state"))  # type: ignore[arg-type]
    title = payload.get("title")
    if state is None or not isinstance(title, str):
        raise ForgeSyncError(
            f"glab mr view payload missing state/title: keys={sorted(payload)}"
        )
    if state == "open" and isinstance(payload.get("draft"), bool):
        state = "draft" if payload["draft"] else "open"

    author_obj = payload.get("author")
    author = author_obj.get("username", "") if isinstance(author_obj, dict) else ""

    checks = parse_glab_pipeline(pipeline, checked_at)
    if checks is not None and pipeline_jobs is not None:
        job_summary = parse_glab_pipeline_jobs(pipeline_jobs, checked_at)
        if job_summary is not None:
            checks = job_summary

    detailed_merge_status = payload.get("detailed_merge_status") or payload.get(
        "merge_status"
    )
    mergeable: bool | None
    if detailed_merge_status in {"mergeable", "can_be_merged"}:
        mergeable = True
    elif detailed_merge_status in {"cannot_be_merged", "broken_status"}:
        mergeable = False
    else:
        # ci_still_running, discussions_not_resolved, ... — not a conflict.
        mergeable = None

    def _opt_str(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    return PullRequestSnapshot(
        state=state,
        title=title,
        source_branch=_opt_str("source_branch") or "",
        target_branch=_opt_str("target_branch") or "",
        author=author,
        created_at=_opt_str("created_at"),
        updated_at=_opt_str("updated_at"),
        closed_at=_opt_str("closed_at"),
        merged_at=_opt_str("merged_at"),
        merge_commit_sha=_opt_str("merge_commit_sha"),
        checks=checks,
        review_decision=_glab_review_decision(detailed_merge_status, approvals),
        mergeable=mergeable,
    )


# --- Command runners (thin; the service calls these in a thread) ---


def _run_command(cmd: list[str], env_name: str, host: str, label: str) -> str:
    """Run a forge CLI command and return stdout; raise on failure.

    Mirrors ``forge.py``: the host env var is pinned to the checkout's remote
    host, the output is decoded as UTF-8 regardless of the locale codepage,
    and a non-zero exit or timeout raises.
    """
    env = dict(os.environ)
    env[env_name] = host
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_FETCH_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ForgeSyncError(
            f"{label} timed out after {_FETCH_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise ForgeSyncError(f"{label} could not be executed: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "rate limit" in stderr.lower() or "API rate limit" in stderr:
            raise RateLimitedSyncError(f"{label} rate limited: {stderr}")
        raise ForgeSyncError(f"{label} failed (exit {result.returncode}): {stderr}")
    return result.stdout


def _parse_json_output(output: str, label: str) -> object:
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ForgeSyncError(f"{label} returned invalid JSON: {exc}") from exc


def fetch_github_pr_snapshot(
    gh_path: str, owner: str, repo: str, host: str, number: int, checked_at: str
) -> PullRequestSnapshot:
    """Fetch one GitHub PR via ``gh pr view`` and parse it."""
    output = _run_command(
        [
            gh_path,
            "pr",
            "view",
            str(number),
            "--repo",
            f"{owner}/{repo}",
            "--json",
            _GH_VIEW_FIELDS,
        ],
        "GH_HOST",
        host,
        f"gh pr view {number}",
    )
    snapshot = parse_github_pr_view(_parse_json_output(output, "gh pr view"))
    if snapshot.checks is not None:
        # Stamp the fetch time onto the parsed summary (the parser runs
        # without one so it stays pure).
        snapshot = replace(
            snapshot,
            checks=ChecksSummary(
                state=snapshot.checks.state,
                checked_at=checked_at,
                checks=snapshot.checks.checks,
            ),
        )
    return snapshot


def fetch_gitlab_mr_snapshot(
    glab_path: str,
    project_path: str,
    host: str,
    iid: int,
    project_id: int | None,
    checked_at: str,
) -> PullRequestSnapshot:
    """Fetch one GitLab MR via ``glab mr view`` plus best-effort side queries.

    The head pipeline (with its jobs when the numeric project id is known)
    and the approvals endpoint are best-effort: a failure there degrades the
    snapshot to "no CI data" / no review decision, it never fails the sync.
    """
    output = _run_command(
        [glab_path, "mr", "view", str(iid), "--repo", project_path, "--output", "json"],
        "GITLAB_HOST",
        host,
        f"glab mr view {iid}",
    )
    payload = _parse_json_output(output, "glab mr view")

    pipeline: object = None
    pipeline_jobs: object = None
    approvals: object = None
    if project_id is not None:
        try:
            mr_api_output = _run_command(
                [glab_path, "api", f"projects/{project_id}/merge_requests/{iid}"],
                "GITLAB_HOST",
                host,
                "glab api merge_request",
            )
            mr_api = _parse_json_output(mr_api_output, "glab api merge_request")
            if isinstance(mr_api, dict):
                pipeline = mr_api.get("head_pipeline")
                pipeline_iid = (
                    pipeline.get("id") if isinstance(pipeline, dict) else None
                )
                if isinstance(pipeline_iid, int):
                    jobs_output = _run_command(
                        [
                            glab_path,
                            "api",
                            f"projects/{project_id}/pipelines/{pipeline_iid}/jobs",
                        ],
                        "GITLAB_HOST",
                        host,
                        "glab api pipeline jobs",
                    )
                    pipeline_jobs = _parse_json_output(
                        jobs_output, "glab api pipeline jobs"
                    )
        except ForgeSyncError as exc:
            logger.debug(
                "GitLab pipeline query failed; syncing without CI data",
                extra={"project_path": project_path, "iid": iid, "reason": str(exc)},
            )
        try:
            approvals_output = _run_command(
                [
                    glab_path,
                    "api",
                    f"projects/{project_id}/merge_requests/{iid}/approvals",
                ],
                "GITLAB_HOST",
                host,
                "glab api approvals",
            )
            approvals = _parse_json_output(approvals_output, "glab api approvals")
        except ForgeSyncError as exc:
            logger.debug(
                "GitLab approvals query failed; syncing without review decision",
                extra={"project_path": project_path, "iid": iid, "reason": str(exc)},
            )

    return parse_glab_mr_view(
        payload,
        pipeline=pipeline,
        pipeline_jobs=pipeline_jobs,
        approvals=approvals,
        checked_at=checked_at,
    )


def fetch_pull_request_snapshot(
    details: GitHubPullRequestDetails | GitLabMergeRequestDetails, checked_at: str
) -> PullRequestSnapshot:
    """Fetch the provider snapshot for one artifact's details.

    Resolves the CLI binary and dispatches on the details variant. Raises
    ``ForgeSyncError`` (or its ``RateLimitedSyncError`` subclass) — the
    service turns failures into "keep the stored state" plus telemetry.
    Synchronous: callers run this in a thread.
    """
    if isinstance(details, GitHubPullRequestDetails):
        gh_path = find_cli_binary("gh")
        if gh_path is None:
            raise ForgeSyncError("gh CLI not found (system or bundled)")
        assert details.pull_request_number is not None
        return fetch_github_pr_snapshot(
            gh_path,
            details.repo_owner,
            details.repo_name,
            details.repo_host,
            details.pull_request_number,
            checked_at,
        )
    glab_path = find_cli_binary("glab")
    if glab_path is None:
        raise ForgeSyncError("glab CLI not found (system or bundled)")
    return fetch_gitlab_mr_snapshot(
        glab_path,
        details.project_path,
        details.repo_host,
        details.merge_request_iid,
        details.repo_id,
        checked_at,
    )
