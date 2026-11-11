"""Forge sync — refresh a PR/MR artifact from its provider (Phase 4).

Phase 4 of session artifacts: for each tracked PR/MR artifact, this module
reads the PR/MR from its provider (one ``gh api graphql`` query on GitHub;
``glab mr view`` plus the pipeline and approvals endpoints on GitLab) and
returns a :class:`PullRequestSnapshot` — the lifecycle state, title,
branches, author, provider timestamps, merge commit SHA, the CI checks
summary, and the sync-time inputs (review decision, mergeability) that feed
the aggregated ``prStatus``.

Only the checks that gate the merge count: GitHub's required checks (one
entry per check, its latest run) and GitLab's jobs that may not fail. The
other checks only set ``optional_checks_status``, which never blocks.

The parsers are pure: functions from CLI JSON output to snapshots, with no
session, no persistence, and no I/O. The thin command runners at the bottom
mirror ``forge.py``'s shape (pinned host env, UTF-8 decode, timeout guard)
and are called in a thread by the artifact service.

Local only. No cloud-sandbox support in Phase 4.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
import subprocess
from typing import Literal, NoReturn

from vibe.core.git.forge import ForgeError
from vibe.core.session.artifact_detector import find_cli_binary
from vibe.core.session.artifacts import (
    ChecksSummary,
    CheckStatus,
    GitLabMergeRequestDetails,
    PrStatus,
    PrStatusReason,
    PullRequestLifecycleState,
)
from vibe.observability.logging import logger

__all__ = [
    "GITHUB_CHECK_BATCH",
    "GITHUB_FULL_READ_BATCH",
    "ForgeSyncError",
    "GitHubPullRequestRef",
    "GitHubQuota",
    "PullRequestSnapshot",
    "RateLimitedSyncError",
    "derive_pr_status",
    "fetch_gitlab_merge_request",
    "parse_github_pull_request",
    "parse_glab_mr_view",
    "parse_glab_pipeline",
    "parse_glab_pipeline_jobs",
    "read_github_checks",
    "read_github_pull_requests",
]

# Generous enough that a slow forge answer is not mistaken for a hang; the
# caller treats a timeout as a failed sync and keeps the stored state.
_FETCH_TIMEOUT_SECONDS = 10


# The fields of a full read. ``isRequired`` is evaluated against the PR's
# branch protection, so only the checks that gate the merge set the checks
# status; GitHub Enterprise Server versions without the field get every check
# (``_HOSTS_WITHOUT_IS_REQUIRED``). The latest check suites carry the
# workflows that failed to start: their required checks never report a run.
# ``mergeStateStatus`` is slow (GitHub computes it on request): a batch of
# full reads stays small (``GITHUB_FULL_READ_BATCH``).
def _gh_pull_request_fields(number: str, *, cursor: str | None, required: bool) -> str:
    is_required = f"isRequired(pullRequestNumber: {number})" if required else ""
    after = f", after: {cursor}" if cursor is not None else ""
    return f"""
      title state isDraft headRefName baseRefName author {{ login }}
      createdAt closedAt mergedAt mergeCommit {{ oid }}
      mergeStateStatus reviewDecision
      commits(last: 1) {{ nodes {{ commit {{
        checkSuites(last: 100) {{
          nodes {{ conclusion workflowRun {{ workflow {{ name }} }} }}
        }}
        statusCheckRollup {{ contexts(first: 100{after}) {{
          pageInfo {{ hasNextPage endCursor }}
          nodes {{
            __typename
            ... on CheckRun {{
              name conclusion startedAt detailsUrl {is_required}
              checkSuite {{ workflowRun {{ workflow {{ name }} }} }}
            }}
            ... on StatusContext {{ context state targetUrl {is_required} }}
          }}
        }} }}
      }} }} }}"""


def _gh_pull_request_query(*, required: bool) -> str:
    """One PR, paginated over the head commit's checks (``gh api graphql
    --paginate`` follows ``$endCursor``).
    """
    fields = _gh_pull_request_fields("$number", cursor="$endCursor", required=required)
    return f"""
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {{
  repository(owner: $owner, name: $repo) {{ pullRequest(number: $number) {{
    {fields}
  }} }}
}}"""


# What changes when a PR's status changes, except the merge state and the
# title. GitHub computes the merge state on request: it reads UNKNOWN at
# first, so it would change the fingerprint without a change of status.
# Cheap: GitHub reads 50 of them in one query in about 3 s, for 1 point.
_GH_CHECK_FIELDS = """
  state isDraft reviewDecision headRefOid
  commits(last: 1) { nodes { commit { statusCheckRollup {
    state
    contexts(first: 0) {
      checkRunCountsByState { state count }
      statusContextCountsByState { state count }
    }
  } } } }"""

GITHUB_CHECK_BATCH = 50
GITHUB_FULL_READ_BATCH = 6

# GitHub Enterprise Server hosts that do not know ``isRequired``: an unknown
# field fails the whole query, so their reads leave it out.
_HOSTS_WITHOUT_IS_REQUIRED: set[str] = set()


class ForgeSyncError(ForgeError):
    """A provider sync fetch failed (no CLI, no auth, command error, bad JSON)."""


class RateLimitedSyncError(ForgeSyncError):
    """The provider rate-limited the request — the caller backs off.

    ``reset_at`` is when the provider restores the quota (epoch seconds), when
    the provider says so; ``None`` falls back to the caller's own backoff.
    """

    def __init__(self, message: str, *, reset_at: float | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


@dataclass(frozen=True)
class GitHubPullRequestRef:
    owner: str
    repo: str
    number: int


@dataclass(frozen=True)
class GitHubQuota:
    """The GraphQL quota after a query: points left of ``limit`` until
    ``reset_at`` (epoch seconds).
    """

    remaining: int
    limit: int
    reset_at: float


@dataclass(frozen=True)
class PullRequestSnapshot:
    """The provider's current truth about one PR/MR.

    ``checks`` covers the checks that gate the merge; it is ``None`` when
    there is no CI data (the checks query failed or the provider offers none),
    and ``ChecksSummary.state == "none"`` means the provider answered and no
    check gates the merge. ``review_decision`` and ``merge_status`` are
    ``None`` when the provider does not say.
    """

    state: Literal["draft", "open", "merged", "closed"]
    title: str
    source_branch: str
    target_branch: str
    author: str
    created_at: str | None = None
    closed_at: str | None = None
    merged_at: str | None = None
    merge_commit_sha: str | None = None
    checks: ChecksSummary | None = None
    # The checks that do not gate the merge; never part of ``checks``.
    optional_checks_state: Literal["pass", "fail", "pending", "none"] | None = None
    review_decision: (
        Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"] | None
    ) = None
    merge_status: Literal["unknown", "mergeable", "blocked", "conflicting"] | None = (
        None
    )


# --- prStatus derivation ---

ChecksWireStatus = Literal["none", "pending", "passing", "failing"]
ReviewWireDecision = Literal["none", "review_required", "changes_requested", "approved"]
MergeWireStatus = Literal["unknown", "mergeable", "blocked", "conflicting"]


def derive_pr_status(
    state: PullRequestLifecycleState,
    checks_status: ChecksWireStatus | None,
    review_decision: ReviewWireDecision | None,
    merge_status: MergeWireStatus | None,
) -> tuple[PrStatus | None, PrStatusReason | None]:
    """The CLI's actionability status and its reason, from the wire's raw statuses.

    Mirrors ``getPullRequestStatus`` in le-chat-web
    (``code/_domain/artifacts/pull-request-status.ts``), collapsed to the
    CLI's three labels. Both sides run the same cases
    (``vibe/tests/core/git/pull_request_status_cases.json``). ``None`` for draft, merged and
    closed PRs, whose lifecycle state is their label, and for an open PR
    whose statuses are not known. The reason is ``None`` unless the status
    is ``action_needed`` or ``waiting``.
    """
    if state != "open":
        return None, None
    # In precedence order: the first match is the status and its reason.
    rules: list[tuple[bool, PrStatus, PrStatusReason]] = [
        (review_decision == "changes_requested", "action_needed", "changes_requested"),
        (checks_status == "failing", "action_needed", "checks_failing"),
        (merge_status == "conflicting", "action_needed", "conflicts"),
        (checks_status == "pending", "waiting", "checks_pending"),
        (review_decision == "review_required", "waiting", "review_required"),
        (merge_status == "blocked", "waiting", "merge_blocked"),
    ]
    for matches, status, reason in rules:
        if matches:
            return status, reason
    if merge_status == "mergeable":
        return "mergeable", None
    return None, None


# --- GitHub parsers (pure) ---

CheckOutcome = Literal["pass", "fail", "pending"]

# A finished run that does not block: GitHub reports a skipped job (its `if`
# was false, or path filters excluded it) and a neutral one as done.
_GH_PASSING_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
_GH_FAILING_CONCLUSIONS = frozenset({
    "FAILURE",
    "TIMED_OUT",
    "CANCELLED",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
})


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


def _gh_check_run_outcome(conclusion: object) -> CheckOutcome:
    if conclusion in _GH_PASSING_CONCLUSIONS:
        return "pass"
    if conclusion in _GH_FAILING_CONCLUSIONS:
        return "fail"
    # No conclusion yet (queued, in progress), or one GitHub added later.
    return "pending"


def _gh_status_context_outcome(state: object) -> CheckOutcome:
    if state == "SUCCESS":
        return "pass"
    if state in {"FAILURE", "ERROR"}:
        return "fail"
    return "pending"


def _gh_merge_status(merge_state_status: object) -> MergeWireStatus | None:
    """Map GitHub's ``mergeStateStatus`` to the provider-neutral value.

    ``UNSTABLE`` (only checks that are not required failed) does not stop the
    merge: those checks are reported apart, as ``optional_checks_status``.
    ``BEHIND`` does: GitHub reports it only when branch protection requires
    an up-to-date branch, and it refuses the merge until the branch is
    updated. ``UNKNOWN`` (GitHub is still computing it) and unexpected values
    map to ``None``.
    """
    match merge_state_status:
        case "CLEAN" | "HAS_HOOKS" | "UNSTABLE":
            return "mergeable"
        case "BLOCKED" | "BEHIND":
            return "blocked"
        case "DIRTY":
            return "conflicting"
        case _:
            return None


@dataclass(frozen=True)
class _GitHubCheck:
    # (workflow, check name): the runs of one check share it.
    key: tuple[str, str]
    name: str
    conclusion: object
    outcome: CheckOutcome
    url: str
    required: bool
    started_at: str


def _github_check(context: object) -> _GitHubCheck | None:
    if not isinstance(context, dict):
        return None
    # Absent when the host does not know the field: every check then counts.
    required = context.get("isRequired") is not False
    match context.get("__typename"):
        case "CheckRun":
            name = context.get("name")
            if not isinstance(name, str):
                return None
            workflow = _github_workflow_name(context.get("checkSuite"))
            url = context.get("detailsUrl")
            started_at = context.get("startedAt")
            return _GitHubCheck(
                key=(workflow, name),
                name=f"{workflow} / {name}" if workflow else name,
                conclusion=context.get("conclusion"),
                outcome=_gh_check_run_outcome(context.get("conclusion")),
                url=url if isinstance(url, str) else "",
                required=required,
                started_at=started_at if isinstance(started_at, str) else "",
            )
        case "StatusContext":
            name = context.get("context")
            if not isinstance(name, str):
                return None
            url = context.get("targetUrl")
            return _GitHubCheck(
                key=("", name),
                name=name,
                conclusion=context.get("state"),
                outcome=_gh_status_context_outcome(context.get("state")),
                url=url if isinstance(url, str) else "",
                required=required,
                started_at="",
            )
        case _:
            return None


def _github_workflow_name(check_suite: object) -> str:
    if not isinstance(check_suite, dict):
        return ""
    run = check_suite.get("workflowRun")
    workflow = run.get("workflow") if isinstance(run, dict) else None
    name = workflow.get("name") if isinstance(workflow, dict) else None
    return name if isinstance(name, str) else ""


def _github_unstarted_workflows(check_suites: object) -> frozenset[str]:
    """The workflows whose latest run on the commit failed to start.

    GitHub lists the commit's check suites oldest first, so a later suite of
    the same workflow (a re-run that started) supersedes the failure.
    """
    if not isinstance(check_suites, list):
        return frozenset()
    latest: dict[str, object] = {}
    for suite in check_suites:
        if not isinstance(suite, dict):
            continue
        workflow = _github_workflow_name(suite)
        if workflow:
            latest[workflow] = suite.get("conclusion")
    return frozenset(
        workflow
        for workflow, conclusion in latest.items()
        if conclusion == "STARTUP_FAILURE"
    )


def _summarize_outcomes(
    outcomes: list[CheckOutcome],
) -> Literal["pass", "fail", "pending", "none"]:
    if not outcomes:
        return "none"
    if "fail" in outcomes:
        return "fail"
    if "pending" in outcomes:
        return "pending"
    return "pass"


def _run_order(check: _GitHubCheck) -> tuple[bool, str]:
    """Sort key of a check's runs: a queued run has not started, so it is the
    newest; the others follow their start time.
    """
    return (check.started_at == "", check.started_at)


def _github_checks(
    contexts: list[object], check_suites: object, checked_at: str
) -> tuple[ChecksSummary, Literal["pass", "fail", "pending", "none"]]:
    """The required checks summary, and the state of the other checks.

    The rollup lists every run of a check (a re-run adds one); the latest
    run is the check's state. A required check skipped because its workflow
    failed to start would pass unseen, so it counts as failed.
    """
    latest: dict[tuple[str, str], _GitHubCheck] = {}
    for context in contexts:
        check = _github_check(context)
        if check is None:
            continue
        previous = latest.get(check.key)
        if previous is None or _run_order(check) >= _run_order(previous):
            latest[check.key] = check
    unstarted = _github_unstarted_workflows(check_suites)
    checks = [
        replace(check, outcome="fail")
        if check.required
        and check.conclusion == "SKIPPED"
        and check.key[0] in unstarted
        else check
        for check in latest.values()
    ]
    required = [check for check in checks if check.required]
    summary = ChecksSummary(
        state=_summarize_outcomes([check.outcome for check in required]),
        checked_at=checked_at,
        checks=[
            CheckStatus(name=check.name, status=check.outcome, url=check.url)
            for check in required
        ],
    )
    return summary, _summarize_outcomes([
        check.outcome for check in checks if not check.required
    ])


def _github_pages(payload: object) -> list[dict[str, object]]:
    """The pull request object of each page of a ``--paginate`` answer."""
    if not isinstance(payload, list) or not payload:
        raise ForgeSyncError("gh api graphql returned no page")
    pull_requests: list[dict[str, object]] = []
    for page in payload:
        data = page.get("data") if isinstance(page, dict) else None
        repository = data.get("repository") if isinstance(data, dict) else None
        pull_request = (
            repository.get("pullRequest") if isinstance(repository, dict) else None
        )
        if not isinstance(pull_request, dict):
            raise ForgeSyncError("gh api graphql returned no pull request")
        pull_requests.append(pull_request)
    return pull_requests


def _github_head_commit(pull_request: dict[str, object]) -> dict[str, object]:
    commits = pull_request.get("commits")
    nodes = commits.get("nodes") if isinstance(commits, dict) else None
    node = nodes[0] if isinstance(nodes, list) and nodes else None
    commit = node.get("commit") if isinstance(node, dict) else None
    return commit if isinstance(commit, dict) else {}


def _github_rollup_contexts(commit: dict[str, object]) -> list[object]:
    rollup = commit.get("statusCheckRollup")
    contexts = rollup.get("contexts") if isinstance(rollup, dict) else None
    nodes = contexts.get("nodes") if isinstance(contexts, dict) else None
    return nodes if isinstance(nodes, list) else []


def parse_github_pull_request(payload: object, checked_at: str) -> PullRequestSnapshot:
    """Parse the pages of a full read (``_gh_pull_request_fields``) into a snapshot.

    Raises :class:`ForgeSyncError` on a malformed payload — the caller keeps
    the stored state and logs the failure.
    """
    pages = _github_pages(payload)
    pull_request = pages[0]
    state = _gh_state(pull_request.get("state"), pull_request.get("isDraft"))
    title = pull_request.get("title")
    if state is None or not isinstance(title, str):
        raise ForgeSyncError(
            f"gh api graphql pull request missing state/title: keys={sorted(pull_request)}"
        )
    head_commit = _github_head_commit(pull_request)
    contexts = [
        context
        for page in pages
        for context in _github_rollup_contexts(_github_head_commit(page))
    ]
    check_suites = head_commit.get("checkSuites")
    checks, optional_checks_state = _github_checks(
        contexts,
        check_suites.get("nodes") if isinstance(check_suites, dict) else None,
        checked_at,
    )

    author_obj = pull_request.get("author")
    author = author_obj.get("login", "") if isinstance(author_obj, dict) else ""
    merge_commit = pull_request.get("mergeCommit")
    merge_commit_sha = (
        merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    )
    review_decision: Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"] | None
    match pull_request.get("reviewDecision"):
        case "APPROVED":
            review_decision = "APPROVED"
        case "CHANGES_REQUESTED":
            review_decision = "CHANGES_REQUESTED"
        case "REVIEW_REQUIRED":
            review_decision = "REVIEW_REQUIRED"
        case _:
            review_decision = None

    def _opt_str(key: str) -> str | None:
        value = pull_request.get(key)
        return value if isinstance(value, str) else None

    return PullRequestSnapshot(
        state=state,
        title=title,
        source_branch=_opt_str("headRefName") or "",
        target_branch=_opt_str("baseRefName") or "",
        author=author if isinstance(author, str) else "",
        created_at=_opt_str("createdAt"),
        closed_at=_opt_str("closedAt"),
        merged_at=_opt_str("mergedAt"),
        merge_commit_sha=merge_commit_sha
        if isinstance(merge_commit_sha, str)
        else None,
        checks=checks,
        optional_checks_state=optional_checks_state,
        review_decision=review_decision,
        merge_status=_gh_merge_status(pull_request.get("mergeStateStatus")),
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

    The pipeline status is the state of the checks that gate the merge:
    GitLab computes it over every job and leaves the jobs allowed to fail
    out. Its one entry, named after the pipeline, gives way to the per-job
    detail when it is fetched (:func:`parse_glab_pipeline_jobs`).
    Returns ``None`` when there is no pipeline object — "no CI data".
    """
    if not isinstance(pipeline, dict):
        return None
    status = pipeline.get("status")
    state: CheckOutcome
    if status in {"success", "skipped"}:
        state = "pass"
    elif status in {"failed", "canceled"}:
        state = "fail"
    else:
        # created, pending, running, manual (waits for a blocking manual job)...
        state = "pending"
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


def _glab_job_outcome(status: object, *, optional: bool) -> CheckOutcome:
    if status in {"success", "skipped"}:
        return "pass"
    if status in {"failed", "canceled"}:
        return "fail"
    if status == "manual" and optional:
        # Nobody has to run it: the pipeline does not wait for it.
        return "pass"
    # created, pending, running, and a blocking manual job, which holds the
    # pipeline until someone runs it.
    return "pending"


def parse_glab_pipeline_jobs(
    jobs: object,
) -> tuple[list[CheckStatus], Literal["pass", "fail", "pending", "none"]] | None:
    """The blocking jobs, and the state of the jobs allowed to fail.

    GitLab lists the latest run of each job (retried runs are excluded by
    default). Returns ``None`` when the listing is unusable — the caller
    keeps the pipeline-level entry (or no summary at all).
    """
    if not isinstance(jobs, list):
        return None
    blocking: list[CheckStatus] = []
    optional: list[CheckOutcome] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = job.get("name")
        if not isinstance(name, str):
            continue
        allowed_to_fail = job.get("allow_failure") is True
        outcome = _glab_job_outcome(job.get("status"), optional=allowed_to_fail)
        if allowed_to_fail:
            optional.append(outcome)
            continue
        web_url = job.get("web_url")
        blocking.append(
            CheckStatus(
                name=name,
                status=outcome,
                url=web_url if isinstance(web_url, str) else "",
            )
        )
    if not blocking and not optional:
        return None
    return blocking, _summarize_outcomes(optional)


def _glab_merge_status(detailed_merge_status: object) -> MergeWireStatus | None:
    """Map GitLab's ``detailed_merge_status`` to the provider-neutral value.

    The statuses that other fields already report (CI, approvals,
    discussions, draft) or that GitLab is still computing map to ``None``.
    ``need_rebase`` is GitHub's ``BEHIND``: GitLab refuses the merge until
    the branch is rebased.
    """
    match detailed_merge_status:
        case "mergeable" | "can_be_merged":
            return "mergeable"
        case "conflict" | "cannot_be_merged" | "broken_status":
            return "conflicting"
        case (
            "need_rebase"
            | "blocked_status"
            | "merge_request_blocked"
            | "external_status_checks"
            | "jira_association_missing"
            | "security_policy_violations"
            | "title_regex"
            | "merge_time"
            | "locked_paths"
            | "locked_lfs_files"
        ):
            return "blocked"
        case _:
            return None


def _glab_review_decision(
    detailed_merge_status: object, approvals: object
) -> Literal["APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"] | None:
    """Map GitLab's merge/approval state onto the review-decision vocabulary.

    GitLab has no reviewDecision, so the mapping is: unresolved discussions
    map to "changes requested", approvals (required or given) map to the
    approval states. Anything unmapped stays ``None``.
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
    optional_checks_state: Literal["pass", "fail", "pending", "none"] | None = None
    jobs = parse_glab_pipeline_jobs(pipeline_jobs)
    if checks is not None and jobs is not None:
        blocking_jobs, optional_checks_state = jobs
        checks = checks.model_copy(update={"checks": blocking_jobs})

    detailed_merge_status = payload.get("detailed_merge_status") or payload.get(
        "merge_status"
    )

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
        closed_at=_opt_str("closed_at"),
        merged_at=_opt_str("merged_at"),
        merge_commit_sha=_opt_str("merge_commit_sha"),
        checks=checks,
        optional_checks_state=optional_checks_state,
        review_decision=_glab_review_decision(detailed_merge_status, approvals),
        merge_status=_glab_merge_status(detailed_merge_status),
    )


# --- Command runners (thin; the service calls these in a thread) ---


def _run_process(
    cmd: list[str], env_name: str, host: str, label: str
) -> subprocess.CompletedProcess[str]:
    """Run a forge CLI command with the host pinned; raise when it cannot run.

    Mirrors ``forge.py``: the host env var is pinned to the checkout's remote
    host, the output is decoded as UTF-8 regardless of the locale codepage,
    and a timeout raises.
    """
    env = dict(os.environ)
    env[env_name] = host
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_FETCH_TIMEOUT_SECONDS,
            check=False,
            env=env,
            # Never inherit stdin: on the stdio transport it is the RPC pipe.
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ForgeSyncError(
            f"{label} timed out after {_FETCH_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise ForgeSyncError(f"{label} could not be executed: {exc}") from exc


def _failure(label: str, result: subprocess.CompletedProcess[str]) -> ForgeSyncError:
    stderr = result.stderr.strip()
    if "rate limit" in stderr.lower():
        return RateLimitedSyncError(f"{label} rate limited: {stderr}")
    return ForgeSyncError(f"{label} failed (exit {result.returncode}): {stderr}")


def _run_command(cmd: list[str], env_name: str, host: str, label: str) -> str:
    """Run a forge CLI command and return stdout; raise on failure."""
    result = _run_process(cmd, env_name, host, label)
    if result.returncode != 0:
        raise _failure(label, result)
    return result.stdout


def _parse_json_output(output: str, label: str) -> object:
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ForgeSyncError(f"{label} returned invalid JSON: {exc}") from exc


def _parse_json_pages(output: str, label: str) -> list[object]:
    """The JSON documents of a ``--paginate`` answer, one per page.

    ``gh api`` writes the pages back to back with no separator (``--slurp``,
    which wraps them in an array, needs gh 2.48, newer than some system
    installs); so do older ``glab api`` versions.
    """
    decoder = json.JSONDecoder()
    pages: list[object] = []
    index = 0
    while index < len(output):
        if output[index].isspace():
            index += 1
            continue
        try:
            page, index = decoder.raw_decode(output, index)
        except json.JSONDecodeError as exc:
            raise ForgeSyncError(f"{label} returned invalid JSON: {exc}") from exc
        pages.append(page)
    return pages


def _github_quota_reset(gh_path: str, host: str) -> float | None:
    """When GitHub restores the GraphQL quota (epoch seconds), if it says.

    ``GET /rate_limit`` does not count against the quota.
    """
    try:
        output = _run_command(
            [gh_path, "api", "rate_limit", "--jq", ".resources.graphql.reset"],
            "GH_HOST",
            host,
            "gh api rate_limit",
        )
    except ForgeSyncError:
        return None
    try:
        return float(output.strip())
    except ValueError:
        return None


def _github_reset(gh_path: str, host: str, error: RateLimitedSyncError) -> NoReturn:
    # A secondary limit (too many requests in a short time) lifts sooner
    # than the quota resets: the caller's fixed delay applies.
    if "secondary rate limit" in str(error).lower():
        raise error
    raise RateLimitedSyncError(
        str(error), reset_at=_github_quota_reset(gh_path, host)
    ) from error


def _github_graphql(
    gh_path: str, host: str, query: str, label: str
) -> dict[str, object]:
    """Run one GraphQL query and return its ``data``.

    An alias that fails alone (a PR that does not exist) is ``null`` in the
    data: the rest of the answer stands, although gh exits with an error.
    """
    result = _run_process(
        [gh_path, "api", "graphql", "-f", f"query={query}"], "GH_HOST", host, label
    )
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    data = payload.get("data") if isinstance(payload, dict) else None
    errors = payload.get("errors") if isinstance(payload, dict) else None
    error_text = json.dumps(errors) if errors else ""
    if "RATE_LIMITED" in error_text:
        _github_reset(gh_path, host, RateLimitedSyncError(f"{label} rate limited"))
    if "isRequired" in error_text and data is None:
        raise _IsRequiredUnknownError(label)
    if isinstance(data, dict):
        return data
    if result.returncode != 0:
        failure = _failure(label, result)
        if isinstance(failure, RateLimitedSyncError):
            _github_reset(gh_path, host, failure)
        raise failure
    raise ForgeSyncError(f"{label} returned no data")


class _IsRequiredUnknownError(ForgeSyncError):
    """The host does not know ``isRequired`` (an older GitHub Enterprise)."""


def _github_quota(data: dict[str, object]) -> GitHubQuota | None:
    rate_limit = data.get("rateLimit")
    if not isinstance(rate_limit, dict):
        return None
    remaining = rate_limit.get("remaining")
    limit = rate_limit.get("limit")
    reset_at = rate_limit.get("resetAt")
    if not (
        isinstance(remaining, int)
        and isinstance(limit, int)
        and isinstance(reset_at, str)
    ):
        return None
    try:
        reset = datetime.fromisoformat(reset_at).timestamp()
    except ValueError:
        return None
    return GitHubQuota(remaining=remaining, limit=limit, reset_at=reset)


def _github_aliases(
    refs: Sequence[GitHubPullRequestRef], fields: Callable[[int], str]
) -> str:
    aliases = " ".join(
        f"p{index}: repository(owner: {json.dumps(ref.owner)},"
        f" name: {json.dumps(ref.repo)}) {{ pullRequest(number: {ref.number})"
        f" {{ {fields(ref.number)} }} }}"
        for index, ref in enumerate(refs)
    )
    return f"query {{ rateLimit {{ remaining limit resetAt }} {aliases} }}"


def _github_alias(data: dict[str, object], index: int) -> dict[str, object] | None:
    repository = data.get(f"p{index}")
    pull_request = (
        repository.get("pullRequest") if isinstance(repository, dict) else None
    )
    return pull_request if isinstance(pull_request, dict) else None


def read_github_checks(
    gh_path: str, host: str, refs: Sequence[GitHubPullRequestRef]
) -> tuple[dict[GitHubPullRequestRef, str | None], GitHubQuota | None]:
    """A fingerprint of each PR's status, in one query (``GITHUB_CHECK_BATCH``).

    The fingerprint changes when the PR's state, review, head commit or
    checks change; not when only its merge state does (the base branch
    moved). ``None`` for a PR that GitHub did not return.
    """
    data = _github_graphql(
        gh_path,
        host,
        _github_aliases(refs, lambda _number: _GH_CHECK_FIELDS),
        f"gh api graphql checks of {len(refs)} pull requests",
    )
    fingerprints: dict[GitHubPullRequestRef, str | None] = {}
    for index, ref in enumerate(refs):
        pull_request = _github_alias(data, index)
        fingerprints[ref] = (
            None
            if pull_request is None
            else json.dumps(pull_request, sort_keys=True, separators=(",", ":"))
        )
    return fingerprints, _github_quota(data)


def read_github_pull_requests(
    gh_path: str, host: str, refs: Sequence[GitHubPullRequestRef], checked_at: str
) -> tuple[
    dict[GitHubPullRequestRef, PullRequestSnapshot | ForgeSyncError], GitHubQuota | None
]:
    """Read the PRs in one query (``GITHUB_FULL_READ_BATCH``) and parse them.

    A PR with more checks than one page holds reads the rest with its own
    paginated query. A PR that fails alone gets its error; a failure of the
    whole query raises.
    """
    required = host.lower() not in _HOSTS_WITHOUT_IS_REQUIRED
    label = f"gh api graphql {len(refs)} pull requests"
    try:
        data = _github_graphql(
            gh_path,
            host,
            _github_aliases(
                refs,
                lambda number: _gh_pull_request_fields(
                    str(number), cursor=None, required=required
                ),
            ),
            label,
        )
    except _IsRequiredUnknownError:
        _HOSTS_WITHOUT_IS_REQUIRED.add(host.lower())
        return read_github_pull_requests(gh_path, host, refs, checked_at)
    results: dict[GitHubPullRequestRef, PullRequestSnapshot | ForgeSyncError] = {}
    for index, ref in enumerate(refs):
        pull_request = _github_alias(data, index)
        if pull_request is None:
            results[ref] = ForgeSyncError(f"{label}: no pull request {ref.number}")
            continue
        pages: list[object] = [{"data": {"repository": {"pullRequest": pull_request}}}]
        try:
            cursor = _github_next_cursor(pull_request)
            if cursor is not None:
                pages.extend(
                    _github_more_checks(gh_path, host, ref, cursor, required=required)
                )
            results[ref] = parse_github_pull_request(pages, checked_at)
        except RateLimitedSyncError:
            raise
        except ForgeSyncError as exc:
            results[ref] = exc
    return results, _github_quota(data)


def _github_next_cursor(pull_request: dict[str, object]) -> str | None:
    rollup = _github_head_commit(pull_request).get("statusCheckRollup")
    contexts = rollup.get("contexts") if isinstance(rollup, dict) else None
    page_info = contexts.get("pageInfo") if isinstance(contexts, dict) else None
    if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    return cursor if isinstance(cursor, str) else None


def _github_more_checks(
    gh_path: str, host: str, ref: GitHubPullRequestRef, cursor: str, *, required: bool
) -> list[object]:
    label = f"gh api graphql pull request {ref.number} checks"
    try:
        output = _run_command(
            [
                gh_path,
                "api",
                "graphql",
                "--paginate",
                "-F",
                f"owner={ref.owner}",
                "-F",
                f"repo={ref.repo}",
                "-F",
                f"number={ref.number}",
                "-f",
                f"endCursor={cursor}",
                "-f",
                f"query={_gh_pull_request_query(required=required)}",
            ],
            "GH_HOST",
            host,
            label,
        )
    except RateLimitedSyncError as exc:
        _github_reset(gh_path, host, exc)
    return _parse_json_pages(output, label)


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
                            "--paginate",
                            f"projects/{project_id}/pipelines/{pipeline_iid}"
                            "/jobs?per_page=100",
                        ],
                        "GITLAB_HOST",
                        host,
                        "glab api pipeline jobs",
                    )
                    # Recent glab merges the pages into one array; older
                    # versions write one array per page.
                    pipeline_jobs = [
                        job
                        for page in _parse_json_pages(
                            jobs_output, "glab api pipeline jobs"
                        )
                        if isinstance(page, list)
                        for job in page
                    ]
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


def fetch_gitlab_merge_request(
    details: GitLabMergeRequestDetails, checked_at: str
) -> PullRequestSnapshot:
    """Read one GitLab MR (GitHub PRs are read in batches,
    :func:`read_github_pull_requests`).

    Raises ``ForgeSyncError`` (or its ``RateLimitedSyncError`` subclass).
    Synchronous: callers run this in a thread.
    """
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
