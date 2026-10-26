"""Session artifact models — mirrors the Vibe Code Web contract.

The wire shape (camelCase) lives in ``ts/packages/code-server-contract/src/artifact/``.
These Python models use snake_case internally and are stored in ``meta.json``.
The RPC layer (``vibe/app_server/protocol.py``) maps them to the camelCase wire
format via dedicated ``Public*`` models.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

__all__ = [
    "ArtifactKind",
    "ArtifactProvider",
    "CheckStatus",
    "ChecksSummary",
    "GitHubPullRequestArtifact",
    "GitHubPullRequestDetails",
    "GitLabMergeRequestArtifact",
    "GitLabMergeRequestDetails",
    "PrStatus",
    "PullRequestLifecycleState",
    "SessionArtifact",
]

ArtifactKind = Literal["pull_request"]
ArtifactProvider = Literal["github", "gitlab"]

# Mirrors pullRequestLifecycleStateSchema in artifact/lifecycle.ts.
PullRequestLifecycleState = Literal["draft", "open", "merged", "closed"]

# Aggregated actionability status, derived server-side at sync time
# (Phase 4) and rendered as-is by every surface. None covers draft,
# merged, closed PRs and artifacts not synced yet.
PrStatus = Literal["action_needed", "waiting", "mergeable"]


class CheckStatus(BaseModel):
    """One CI check entry (Phase 4, ``checks`` detail)."""

    name: str
    status: Literal["pass", "fail", "pending"]
    url: str = ""


class ChecksSummary(BaseModel):
    """CI checks summary (Phase 4, the FR3.1 detail).

    ``None`` on the artifact means "no CI data" (the provider has no checks,
    or the checks query failed); ``state: "none"`` means the provider
    answered and there are no checks.
    """

    state: Literal["pass", "fail", "pending", "none"]
    checked_at: str = ""
    checks: list[CheckStatus] = Field(default_factory=list)


class _PullRequestSharedEnrichment(BaseModel):
    """Enrichment fields populated by provider sync (Phase 4).

    Required keys since Phase 4: every artifact is enriched from birth (the
    creation/discovery path fetches the PR/MR immediately), so the fields are
    always written together. Fields that are genuinely absent for open PRs
    stay nullable but required (present with value None). ``pr_status`` and
    ``checks`` are the two agreed additions: the aggregated actionability
    status (rendered by every surface) and the CI detail (rendered in the
    CLI artifacts modal). The sync-time inputs (review decision, mergeable)
    are deliberately not stored.
    """

    source_branch: str
    target_branch: str
    author: str
    state: PullRequestLifecycleState
    provider_created_at: str | None
    provider_updated_at: str | None
    provider_closed_at: str | None
    provider_merged_at: str | None
    merge_commit_sha: str | None
    pr_status: PrStatus | None = None
    checks: ChecksSummary | None = None
    # Raw provider-neutral statuses (the wire's derived-status source):
    # checks_status none/pending/passing/failing, review_decision
    # none/review_required/changes_requested/approved, merge_status
    # unknown/mergeable/blocked/conflicting. Filled from the same snapshot
    # as ``checks``; the client derives the actionability status from them.
    checks_status: str | None = None
    review_decision: str | None = None
    merge_status: str | None = None


class GitHubPullRequestDetails(_PullRequestSharedEnrichment):
    """GitHub-specific artifact details. Mirrors GitHubPullRequestDetailsSchema."""

    kind: Literal["pull_request"] = "pull_request"
    schema_version: int = 1
    repo_host: str
    repo_owner: str
    repo_name: str
    pull_request_number: int | None = None


class GitLabMergeRequestDetails(_PullRequestSharedEnrichment):
    """GitLab-specific artifact details. Mirrors GitLabMergeRequestDetailsSchema.

    ``repo_id`` is required by the Code Web contract but optional here as a
    documented deviation: when ``glab`` is unavailable (dev mode, no bundled
    binary), the detector falls back to ``project_path`` in the external key
    and creates the artifact without a resolved ``repo_id``. The wire model
    omits ``repoId`` in that case.
    """

    kind: Literal["pull_request"] = "pull_request"
    schema_version: int = 1
    repo_host: str
    repo_id: int | None = None
    project_path: str
    merge_request_iid: int


class _SessionArtifactBase(BaseModel):
    """Common fields shared by all artifact variants. Mirrors sessionArtifactCommonFields."""

    id: str  # UUID v7 (time-ordered), generated via uuid-utils
    session_id: str
    # Stable dedupe key: "github.com/org/repo/pull/123" or "gitlab.com/42/merge_requests/7"
    external_key: str
    external_url: str
    display_title: str | None = None
    discovered_at: str  # ISO 8601 — when the session first observed this artifact
    viewed_at: str | None = None  # ISO 8601 — populated in Phase 3 (markViewed)


class GitHubPullRequestArtifact(_SessionArtifactBase):
    """GitHub pull request artifact. Mirrors GitHubPullRequestArtifactJSONSchema."""

    kind: Literal["pull_request"] = "pull_request"
    provider: Literal["github"] = "github"
    details: GitHubPullRequestDetails | None = None


class GitLabMergeRequestArtifact(_SessionArtifactBase):
    """GitLab merge request artifact. Mirrors GitLabMergeRequestArtifactJSONSchema."""

    kind: Literal["pull_request"] = "pull_request"
    provider: Literal["gitlab"] = "gitlab"
    details: GitLabMergeRequestDetails | None = None


# Discriminated union on ``provider`` — mirrors PullRequestArtifactJSONSchema
# (z.discriminatedUnion("provider", …)) in the Code Web contract.
SessionArtifact = Annotated[
    GitHubPullRequestArtifact | GitLabMergeRequestArtifact,
    Field(discriminator="provider"),
]


def sanitize_artifacts_in_raw_metadata(
    raw: dict[str, object], *, session_dir: object = None
) -> dict[str, object]:
    """Drop persisted artifacts that fail the current model, in place.

    Load-time resilience (Phase 4): an artifact written by an older build
    (pre-strict enrichment fields) or corrupted on disk must not fail the
    whole session-metadata parse — it is dropped with a logged warning and
    the session loads without it. Returns the same mapping for chaining.
    """
    from vibe.observability.logging import logger

    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, list):
        return raw
    kept: list[object] = []
    for entry in artifacts:
        try:
            TypeAdapter(SessionArtifact).validate_python(entry)
        except ValidationError as exc:
            logger.warning(
                "Dropping invalid persisted session artifact",
                extra={
                    "session_dir": str(session_dir) if session_dir else None,
                    "error": str(exc)[:500],
                },
            )
            continue
        kept.append(entry)
    raw["artifacts"] = kept
    return raw
