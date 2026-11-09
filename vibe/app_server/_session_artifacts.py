"""Session artifact lifecycle — creation, dedup, persistence.

Wraps ``vibe.core.session.artifact_detector`` for one session: inspects finished
shell effects, builds ``SessionArtifact`` models (mirroring the Vibe Code Web
contract), dedupes on a stable ``external_key``, and persists them into the
session's ``meta.json`` under ``SessionMetadata.artifacts``.

Persistence is best-effort: a corrupted ``meta.json`` must never break session
event processing — the in-memory artifact survives and the caller is still
notified.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
import dataclasses
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from giturlparse import parse as parse_git_url
from uuid_utils import uuid7

from vibe.app_server._dispatch import DispatchResult, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._pull_request_cache import PullRequestDetails, pull_request_cache
from vibe.app_server.protocol import (
    ArtifactsListParams,
    ArtifactsListResponse,
    ArtifactsMarkViewedParams,
    EmptyResponse,
    PublicChecksSummary,
    PublicCheckStatus,
    PublicGitHubPullRequestDetails,
    PublicGitLabMergeRequestDetails,
    PublicPullRequestArtifactDetails,
    PublicSessionArtifact,
    PublicWorktreeArtifact,
)
from vibe.core.git.forge import (
    DiscoveredPullRequest,
    ForgeError,
    PullRequestHead,
    discover_pull_requests,
)
from vibe.core.git.forge_sync import (
    ChecksWireStatus,
    ReviewWireDecision,
    derive_pr_status,
)
from vibe.core.git.remote import parse_github_url
from vibe.core.git.worktree.repository import WorktreeRepository
from vibe.core.session.artifact_detector import (
    DetectedPullRequest,
    cli_binary_source,
    extract_draft_from_command,
    extract_prs_from_output,
    is_pr_creation_command,
    pr_creation_limit,
    pr_creation_output_first,
    resolve_gitlab_repo_id,
)
from vibe.core.session.artifacts import (
    ChecksSummary,
    GitHubPullRequestArtifact,
    GitHubPullRequestDetails,
    GitLabMergeRequestArtifact,
    GitLabMergeRequestDetails,
    PullRequestLifecycleState,
    SessionArtifact,
    WorktreeArtifact,
)
from vibe.core.session.metadata import persist_metadata
from vibe.core.session.session_index import METADATA_FILENAME
from vibe.core.telemetry.session import SessionTelemetry
from vibe.core.types import SessionMetadata
from vibe.observability.logging import logger
from vibe.utils.io import read_safe

__all__ = [
    "ArtifactRequestHandler",
    "SessionArtifactService",
    "to_public_artifact",
    "with_cached_status",
]

# Only finished effects can carry a creation signal; a running or pending
# effect has no output to parse yet.
_PROCESSABLE_STATUSES = frozenset({"completed", "failed"})

# Telemetry payload vocabularies — enums and counters only, no URLs, repo names, PR
# numbers or hosts.
_DetectionStage = Literal[
    "command_parse",
    "output_extract",
    "repo_id_resolve",
    "coordinates_resolve",
    "persist",
]
_DetectionProvider = Literal["github", "gitlab", "unknown"]


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


_CHECKS_WIRE_STATUS: dict[str, ChecksWireStatus] = {
    "pass": "passing",
    "fail": "failing",
    "pending": "pending",
    "none": "none",
}


# The provider's uppercase review-decision form, mapped to the wire's
# provider-neutral vocabulary (artifact/status.ts). Anything else — an
# unknown value or a missing decision — stores ``None``.
_REVIEW_DECISION_WIRE: dict[str, ReviewWireDecision] = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "REVIEW_REQUIRED": "review_required",
}


def with_cached_status(artifact: SessionArtifact) -> SessionArtifact:
    """The artifact with its status from the process's pull request cache.

    ``provider_updated_at`` is when the status was last read; ``pr_status``
    (the CLI's label) is derived from the same raw statuses as the clients'
    status. Without a cached read, the artifact stands as stored.
    """
    details = artifact.details
    cached = pull_request_cache().get(artifact.external_key)
    if details is None or cached is None:
        return artifact
    snapshot = cached.snapshot
    checks_status = (
        None if snapshot.checks is None else _CHECKS_WIRE_STATUS[snapshot.checks.state]
    )
    review_decision = _REVIEW_DECISION_WIRE.get(snapshot.review_decision or "")
    pr_status, pr_status_reason = derive_pr_status(
        snapshot.state, checks_status, review_decision, snapshot.merge_status
    )
    return artifact.model_copy(
        update={
            "display_title": snapshot.title or artifact.display_title,
            "details": details.model_copy(
                update={
                    "state": snapshot.state,
                    "source_branch": snapshot.source_branch or details.source_branch,
                    "target_branch": snapshot.target_branch or details.target_branch,
                    "author": snapshot.author or details.author,
                    "provider_created_at": snapshot.created_at,
                    "provider_updated_at": cached.read_at,
                    "provider_closed_at": snapshot.closed_at,
                    "provider_merged_at": snapshot.merged_at,
                    "merge_commit_sha": snapshot.merge_commit_sha,
                    "pr_status": pr_status,
                    "pr_status_reason": pr_status_reason,
                    "checks": snapshot.checks,
                    "checks_status": checks_status,
                    "optional_checks_status": (
                        None
                        if snapshot.optional_checks_state is None
                        else _CHECKS_WIRE_STATUS[snapshot.optional_checks_state]
                    ),
                    "review_decision": review_decision,
                    "merge_status": snapshot.merge_status,
                }
            ),
        }
    )


def _artifact_id(session_id: str, external_key: str) -> str:
    """One id per PR/MR per session, whichever service instance records it."""
    return str(uuid5(NAMESPACE_URL, f"{session_id}/{external_key}"))


def _github_external_key(host: str, owner: str, repo: str, number: int) -> str:
    return f"{host}/{owner}/{repo}/pull/{number}".lower()


def _gitlab_external_key(
    host: str, repo_id: int | None, project_path: str, iid: int
) -> str:
    """Stable dedupe key for a GitLab MR.

    Prefers the numeric ``repo_id`` (stable across group/project renames);
    falls back to ``project_path`` when ``glab`` could not resolve it.
    """
    if repo_id is not None:
        return f"{host}/{repo_id}/merge_requests/{iid}".lower()
    return f"{host}/{project_path}/-/merge_requests/{iid}".lower()


def _gitlab_mr_identity(host: str, project_path: str, iid: int) -> tuple[str, str, int]:
    """Identity for GitLab dedup that does not depend on repo_id resolution."""
    return (host.lower(), project_path.lower(), iid)


def _artifact_gitlab_identity(artifact: SessionArtifact) -> tuple[str, str, int] | None:
    if not isinstance(artifact, GitLabMergeRequestArtifact):
        return None
    details = artifact.details
    if details is None:
        return None
    return _gitlab_mr_identity(
        details.repo_host, details.project_path, details.merge_request_iid
    )


def _external_key_from_discovered(
    pr: DiscoveredPullRequest, repo_id: int | None = None
) -> str:
    """Compute the external key for a discovered PR/MR (Phase 2).

    Same format as Phase 1 so dedup works across both sources: a GitLab MR
    prefers the numeric ``repo_id`` (resolved best-effort) and falls back to
    the project path, exactly like ``_compute_external_key``.
    """
    if pr.provider == "github":
        return _github_external_key(
            pr.repo_host, pr.repo_owner, pr.repo_name, pr.number
        )
    return _gitlab_external_key(pr.repo_host, repo_id, pr.repo_owner, pr.number)


class SessionArtifactService:
    """Manages the artifact lifecycle for a single session."""

    def __init__(
        self,
        session_id: str,
        session_dir: Path,
        *,
        telemetry: SessionTelemetry | None = None,
        metadata_lock: asyncio.Lock | None = None,
        on_artifacts_changed: Callable[
            [list[SessionArtifact], list[WorktreeArtifact]], None
        ]
        | None = None,
    ) -> None:
        self._session_id = session_id
        self._session_dir = session_dir
        # Keyed by external_key — the stable dedupe key from the contract.
        self._artifacts: dict[str, SessionArtifact] = {}
        self._worktrees: dict[str, WorktreeArtifact] = {}
        self._dirty = False
        # Emission surface for the vibe.artifact_* events; None when the
        # session telemetry is unavailable (unit tests, non-adapter callers).
        self._telemetry = telemetry
        # Shared with the session adapter's metadata writes so artifact RMW
        # cannot race a full sidecar persist (and so in-memory metadata stays
        # aligned under the same lock).
        self._metadata_lock = metadata_lock
        self._on_artifacts_changed = on_artifacts_changed

    def load(self, metadata: SessionMetadata) -> None:
        """Seed from the session's persisted metadata (session start/resume).

        Synchronous: seeding reads the in-memory metadata model, no I/O.
        """
        for artifact in metadata.artifacts:
            self._artifacts[artifact.external_key] = artifact
        for worktree in metadata.worktrees:
            self._worktrees[worktree.path] = worktree
        self._dirty = False

    def list_artifacts(self) -> list[SessionArtifact]:
        """All artifacts, ordered by observation time then id."""
        return sorted(self._artifacts.values(), key=lambda a: (a.discovered_at, a.id))

    def list_worktrees(self) -> list[WorktreeArtifact]:
        return sorted(self._worktrees.values(), key=lambda w: (w.discovered_at, w.id))

    async def record_worktree(
        self, *, path: Path, branch: str, repo_root: Path
    ) -> WorktreeArtifact | None:
        """Record a worktree the session created; None when already recorded."""
        key = str(path)
        if key in self._worktrees:
            return None
        worktree = WorktreeArtifact(
            id=str(uuid7()),
            session_id=self._session_id,
            path=key,
            branch=branch,
            repo_root=str(repo_root),
            discovered_at=_utcnow_iso(),
        )
        self._worktrees[key] = worktree
        self._dirty = True
        try:
            await self._persist()
        except Exception:
            logger.warning(
                "Failed to persist a session worktree; keeping in-memory copy",
                extra={"session_id": self._session_id},
                exc_info=True,
            )
        return worktree

    async def forget_worktree(self, path: Path) -> None:
        if self._worktrees.pop(str(path), None) is None:
            return
        self._dirty = True
        try:
            await self._persist()
        except Exception:
            logger.warning(
                "Failed to persist a forgotten session worktree",
                extra={"session_id": self._session_id},
                exc_info=True,
            )

    async def mark_viewed(self, artifact_ids: list[str]) -> list[SessionArtifact]:
        """Mark artifacts as viewed (Phase 3: the Desktop artifact panel).

        Idempotent and forgiving, mirroring the Code Web fetcher
        (``markViewedByIds`` with ``ignoreNotFound``): unknown ids are a
        silent no-op and an already-viewed artifact keeps its original
        ``viewed_at``. Returns the artifacts this call newly marked, in
        request order. Persistence is best-effort (B9): a failed write keeps
        the in-memory update.
        """
        by_id = {
            artifact.id: (key, artifact) for key, artifact in self._artifacts.items()
        }
        marked: list[SessionArtifact] = []
        already_viewed = 0
        for artifact_id in artifact_ids:
            entry = by_id.get(artifact_id)
            if entry is None:
                continue  # unknown id: silent no-op (cloud parity)
            external_key, artifact = entry
            if artifact.viewed_at is not None:
                already_viewed += 1
                continue
            artifact = artifact.model_copy(update={"viewed_at": _utcnow_iso()})
            self._artifacts[external_key] = artifact
            marked.append(artifact)
        if marked:
            self._dirty = True
            try:
                await self._persist()
            except Exception:
                logger.warning(
                    "Failed to persist viewed artifacts; keeping in-memory copy",
                    extra={"session_id": self._session_id},
                    exc_info=True,
                )
        self._record_mark_viewed(
            nb_artifacts=len(artifact_ids), nb_already_viewed=already_viewed
        )
        return marked

    async def discover_and_create(self, repo_path: Path) -> list[SessionArtifact]:
        """Discover open PRs/MRs linked to *repo_path*'s current branch.

        Phase 2: called at session load (start + resume) and whenever the
        session moves to another checkout. Runs ``gh pr list --head <branch>``
        / ``glab mr list --source-branch <branch>`` in a thread, then creates
        a ``SessionArtifact`` per discovered PR/MR. Existing artifacts are
        kept, and a head (host, repository, branch) one of them already
        covers is not queried again while that PR/MR is open: a merged or
        closed one's branch can get a new PR/MR.

        Returns the list of artifacts created (empty when skipped, no PRs
        found, or discovery failed). Persists all created artifacts in one
        ``meta.json`` write. Never raises — discovery is best-effort.
        """
        # The state as last read, not the stored one (the state at creation):
        # from memory for a watched PR, else a small file read, off the loop.
        cache = pull_request_cache()
        artifacts = list(self._artifacts.values())
        reads = await asyncio.to_thread(
            lambda: [cache.get(artifact.external_key) for artifact in artifacts]
        )
        known_heads = {
            head
            for artifact, read in zip(artifacts, reads, strict=True)
            if artifact.details is not None
            and (read.snapshot.state if read else artifact.details.state)
            in {"draft", "open"}
            and (head := _artifact_head(artifact)) is not None
        }
        try:
            discovered = await asyncio.to_thread(
                discover_pull_requests, repo_path, skip_heads=known_heads
            )
        except ForgeError as exc:
            logger.info(
                "PR discovery skipped",
                extra={"session_id": self._session_id, "reason": str(exc)},
            )
            return []
        except Exception:
            logger.warning(
                "PR discovery failed unexpectedly",
                extra={"session_id": self._session_id},
                exc_info=True,
            )
            return []

        # GitLab: resolve the numeric repo_id once, best-effort (every
        # discovered MR shares the repo), so discovered MRs use the same
        # external key as Phase 1 detection — the keys must match for
        # cross-phase dedup when a creation races discovery.
        repo_id: int | None = None
        gitlab_mr = next((pr for pr in discovered if pr.provider == "gitlab"), None)
        if gitlab_mr is not None:
            repo_id = await resolve_gitlab_repo_id(
                gitlab_mr.repo_host, gitlab_mr.repo_owner
            )

        created: list[SessionArtifact] = []
        for pr in discovered:
            external_key = _external_key_from_discovered(pr, repo_id)
            if self._holds(external_key, pr.url):
                continue
            artifact = self._build_artifact_from_discovered(pr, external_key, repo_id)
            self._artifacts[external_key] = artifact
            created.append(artifact)

        if created:
            self._dirty = True
            try:
                await self._persist()
            except Exception:
                logger.warning(
                    "Failed to persist discovered artifacts; keeping in-memory copy",
                    extra={"session_id": self._session_id},
                    exc_info=True,
                )
            for artifact in created:
                self._record_created(artifact, message_id=None, source="discovered")
        return [await self._read_new_artifact(artifact) for artifact in created]

    def _holds(self, external_key: str, url: str) -> bool:
        """Whether this PR/MR is already recorded.

        The URL catches the same MR stored under the other key form: one
        phase resolved a GitLab ``repo_id`` that the other could not.
        """
        return external_key in self._artifacts or any(
            a.external_url == url for a in self._artifacts.values()
        )

    def _build_artifact_from_discovered(
        self, pr: DiscoveredPullRequest, external_key: str, repo_id: int | None = None
    ) -> SessionArtifact:
        """Build a SessionArtifact from a discovered PR/MR (Phase 2).

        Richer than Phase 1: title, author, state, and branches come from the
        forge API response.
        """
        discovered_at = _utcnow_iso()
        if pr.provider == "github":
            return GitHubPullRequestArtifact(
                id=_artifact_id(self._session_id, external_key),
                session_id=self._session_id,
                external_key=external_key,
                external_url=pr.url,
                display_title=pr.title,
                discovered_at=discovered_at,
                details=GitHubPullRequestDetails(
                    repo_host=pr.repo_host,
                    repo_owner=pr.repo_owner,
                    repo_name=pr.repo_name,
                    pull_request_number=pr.number,
                    state=pr.state,
                    source_branch=pr.source_branch,
                    target_branch=pr.target_branch,
                    author=pr.author,
                    provider_created_at=None,
                    provider_updated_at=None,
                    provider_closed_at=None,
                    provider_merged_at=None,
                    merge_commit_sha=None,
                    pr_status=None,
                    checks=None,
                ),
            )
        return GitLabMergeRequestArtifact(
            id=_artifact_id(self._session_id, external_key),
            session_id=self._session_id,
            external_key=external_key,
            external_url=pr.url,
            display_title=pr.title,
            discovered_at=discovered_at,
            details=GitLabMergeRequestDetails(
                repo_host=pr.repo_host,
                repo_id=repo_id,
                project_path=pr.repo_owner,
                merge_request_iid=pr.number,
                state=pr.state,
                source_branch=pr.source_branch,
                target_branch=pr.target_branch,
                author=pr.author,
                provider_created_at=None,
                provider_updated_at=None,
                provider_closed_at=None,
                provider_merged_at=None,
                merge_commit_sha=None,
                pr_status=None,
                checks=None,
            ),
        )

    def watched(self) -> list[tuple[str, PullRequestDetails]]:
        """The PRs/MRs to keep fresh, for :meth:`PullRequestCache.watch`."""
        return [
            (artifact.external_key, artifact.details)
            for artifact in self._artifacts.values()
            if artifact.details is not None
        ]

    async def _read_new_artifact(self, artifact: SessionArtifact) -> SessionArtifact:
        """Read a freshly built artifact's PR/MR before it is announced.

        Creation and discovery each read the PR/MR once, so the announced
        artifact carries its status (``to_public_artifact`` reads it from the
        cache). A failure is not fatal: the artifact stands with the partial
        data it was built with, and the next refresh fills the rest.
        """
        details = artifact.details
        if details is None:
            return artifact
        try:
            await pull_request_cache().read_now(artifact.external_key, details)
        except Exception:
            logger.debug(
                "Initial provider read failed; keeping partial artifact",
                extra={"session_id": self._session_id, "provider": artifact.provider},
                exc_info=True,
            )
        return artifact

    async def on_shell_effect(
        self,
        command: str,
        stdout: str,
        stderr: str,
        status: str,
        *,
        message_id: str | None = None,
        cwd: str | None = None,
    ) -> list[SessionArtifact]:
        """Inspect a finished shell effect. Returns the created artifacts.

        Called by the adapter when a ``bash``/``shell`` tool effect reaches a
        terminal state. One effect may create several PRs/MRs (a loop over
        ``gh pr create``). Returns an empty list when the effect is not a
        PR/MR creation or reopen, carries no provider URL, or every artifact
        already exists (dedup).

        *cwd* is the session's checkout: a reopen confirmation carries no
        URL, so missing coordinates (host, owner/repo, project path) are
        resolved from its remote.

        Emits ``vibe.artifact_detected`` when a PR/MR creation command is
        intercepted and ``vibe.artifact_created`` once the artifact is
        recorded. An unexpected error emits ``vibe.artifact_detection_failed``
        with the stage it failed at, then re-raises so the adapter's
        best-effort guard (B9) logs it — except persist failures, which stay
        local so the in-memory artifact still stands and the caller notifies.
        """
        if status not in _PROCESSABLE_STATUSES:
            return []
        stage: _DetectionStage = "command_parse"
        provider: _DetectionProvider = "unknown"
        try:
            detected_provider = is_pr_creation_command(command)
            if detected_provider is None:
                return []
            provider = detected_provider
            self._record_detected(detected_provider, message_id=message_id)
            stage = "output_extract"
            created: list[SessionArtifact] = []
            for detected in extract_prs_from_output(
                stdout,
                stderr,
                provider=detected_provider,
                limit=pr_creation_limit(command),
                from_start=pr_creation_output_first(command),
            ):
                # GitLab: resolve the numeric repo_id best-effort via glab api
                # so the external key survives group/project renames.
                if (
                    detected.provider == "gitlab"
                    and detected.repo_id is None
                    and detected.project_path is not None
                ):
                    stage = "repo_id_resolve"
                    repo_id = await resolve_gitlab_repo_id(
                        detected.host, detected.project_path
                    )
                    if repo_id is not None:
                        detected = dataclasses.replace(detected, repo_id=repo_id)

                # A reopen confirmation carries no URL: resolve the missing
                # coordinates (host, owner/repo, project path) from the
                # session's checkout remote.
                if detected.url == "" or not _has_complete_coordinates(detected):
                    stage = "coordinates_resolve"
                    resolved = await self._resolve_remote_coordinates(detected, cwd)
                    if resolved is None:
                        logger.info(
                            "PR coordinates could not be resolved from the checkout",
                            extra={"session_id": self._session_id},
                        )
                        continue
                    detected = resolved

                # The URL regexes guarantee these; the guard keeps the builder type-safe.
                if not _has_complete_coordinates(detected):
                    continue

                stage = "persist"
                artifact = await self._record_new_artifact(
                    detected, command, message_id=message_id
                )
                if artifact is not None:
                    created.append(artifact)
            return created
        except Exception as exc:
            self._record_detection_failed(exc, provider=provider, stage=stage)
            raise

    async def _resolve_remote_coordinates(
        self, detected: DetectedPullRequest, cwd: str | None
    ) -> DetectedPullRequest | None:
        """Fill a reopen detection's missing coordinates from the checkout.

        Reads the remote of the git repository at *cwd* in a thread. GitHub
        yields owner/repo/host and rebuilds the pull URL; GitLab yields the
        project path, host, and merge-request URL. Returns None when the
        remote is missing or does not match the detected provider — the
        caller skips the artifact rather than storing wrong coordinates.
        """
        if cwd is None:
            return None

        def _read_remote_url() -> str | None:
            try:
                with WorktreeRepository.open(Path(cwd)) as repo:
                    return repo.status().repo_url
            except Exception:
                logger.debug(
                    "Could not read the checkout remote for artifact detection",
                    extra={"session_id": self._session_id},
                    exc_info=True,
                )
                return None

        remote_url = await asyncio.to_thread(_read_remote_url)
        if not remote_url:
            return None

        if detected.provider == "github":
            parsed = parse_github_url(remote_url)
            if parsed is None or detected.number is None:
                return None
            owner, repo = parsed
            host = parse_git_url(remote_url).host or "github.com"
            url = f"https://{host}/{owner}/{repo}/pull/{detected.number}"
            return dataclasses.replace(
                detected, url=url, host=host, owner=owner, repo=repo
            )

        url_parsed = parse_git_url(remote_url)
        if (
            not url_parsed.valid
            or "gitlab" not in (url_parsed.host or "").lower()
            or detected.iid is None
        ):
            return None
        host = url_parsed.host or "gitlab.com"
        path_parts = [
            part
            for part in (url_parsed.owner, *url_parsed.groups, url_parsed.repo)
            if part
        ]
        project_path = "/".join(path_parts)
        url = f"https://{host}/{project_path}/-/merge_requests/{detected.iid}"
        return dataclasses.replace(
            detected, url=url, host=host, project_path=project_path
        )

    @asynccontextmanager
    async def _acquire_metadata_lock(self) -> AsyncGenerator[None]:
        if self._metadata_lock is None:
            yield
            return
        async with self._metadata_lock:
            yield

    def _find_duplicate_key(self, detected: DetectedPullRequest) -> str | None:
        """Return an existing map key for this PR/MR, if any.

        GitLab ``external_key`` may use either ``repo_id`` or ``project_path``
        depending on whether ``glab api`` resolved; dedup by MR identity so the
        same merge request is never stored twice under both forms. The URL
        catches any other key form, as discovery's ``_holds`` does.
        """
        preferred = _compute_external_key(detected)
        if preferred in self._artifacts:
            return preferred
        if detected.url:
            for key, artifact in self._artifacts.items():
                if artifact.external_url == detected.url:
                    return key
        if detected.provider != "gitlab":
            return None
        assert detected.project_path is not None
        assert detected.iid is not None
        identity = _gitlab_mr_identity(
            detected.host, detected.project_path, detected.iid
        )
        for key, artifact in self._artifacts.items():
            if _artifact_gitlab_identity(artifact) == identity:
                return key
        return None

    async def _record_new_artifact(
        self, detected: DetectedPullRequest, command: str, *, message_id: str | None
    ) -> SessionArtifact | None:
        """Enrich, insert, persist, and notify under the optional metadata lock."""
        external_key = _compute_external_key(detected)
        artifact = self._build_artifact(detected, command, external_key)

        async with self._acquire_metadata_lock():
            duplicate_key = self._find_duplicate_key(detected)
            if duplicate_key is not None:
                logger.debug(
                    "Artifact already recorded, skipping duplicate",
                    extra={
                        "artifact_external_key": duplicate_key,
                        "session_id": self._session_id,
                    },
                )
                return None

            self._artifacts[external_key] = artifact
            self._dirty = True
            persist_error: BaseException | None = None
            try:
                await self._persist_unlocked()
            except Exception as exc:
                persist_error = exc

        if persist_error is not None:
            logger.warning(
                "Failed to persist session artifacts; keeping in-memory copy",
                extra={"session_id": self._session_id},
                exc_info=persist_error,
            )
            self._record_detection_failed(
                persist_error, provider=detected.provider, stage="persist"
            )
        self._record_created(artifact, message_id=message_id)
        return await self._read_new_artifact(artifact)

    def _record_detected(
        self, provider: Literal["github", "gitlab"], *, message_id: str | None
    ) -> None:
        if self._telemetry is None:
            return
        cli_name = "gh" if provider == "github" else "glab"
        self._telemetry.record_artifact_detected(
            provider=provider,
            cli_source=cli_binary_source(cli_name),
            command_matched=True,
            message_id=message_id,
        )

    def _record_created(
        self,
        artifact: SessionArtifact,
        *,
        message_id: str | None,
        source: Literal["detected", "discovered"] = "detected",
    ) -> None:
        if self._telemetry is None:
            return
        self._telemetry.record_artifact_created(
            provider=artifact.provider,
            is_draft=_artifact_is_draft(artifact),
            host_type=_artifact_host_type(artifact),
            repo_id_resolved=_artifact_repo_id_resolved(artifact),
            has_title=artifact.display_title is not None,
            message_id=message_id,
            source=source,
        )

    def _record_mark_viewed(self, *, nb_artifacts: int, nb_already_viewed: int) -> None:
        """Emit ``vibe.artifact_mark_viewed``."""
        if self._telemetry is None:
            return
        self._telemetry.record_artifact_mark_viewed(
            nb_artifacts=nb_artifacts, nb_already_viewed=nb_already_viewed
        )

    def _record_detection_failed(
        self,
        error: BaseException,
        *,
        provider: _DetectionProvider,
        stage: _DetectionStage,
    ) -> None:
        if self._telemetry is None:
            return
        self._telemetry.record_artifact_detection_failed(
            error_type=type(error).__name__, provider=provider, stage=stage
        )

    def _build_artifact(
        self, detected: DetectedPullRequest, command: str, external_key: str
    ) -> SessionArtifact:
        # The title comes from the provider read that enriches the artifact
        # (enrich-from-birth): the command text cannot be read reliably (a
        # heredoc, a variable, shell quoting).
        draft = extract_draft_from_command(command, detected.provider)
        state: PullRequestLifecycleState = "draft" if draft else "open"
        discovered_at = _utcnow_iso()

        if detected.provider == "github":
            assert detected.owner is not None  # guarded by the caller
            assert detected.repo is not None
            assert detected.number is not None
            return GitHubPullRequestArtifact(
                id=_artifact_id(self._session_id, external_key),
                session_id=self._session_id,
                external_key=external_key,
                external_url=detected.url,
                display_title=None,
                discovered_at=discovered_at,
                details=GitHubPullRequestDetails(
                    repo_host=detected.host,
                    repo_owner=detected.owner,
                    repo_name=detected.repo,
                    pull_request_number=detected.number,
                    state=state,
                    # The enrichment fetch (enrich-from-birth) fills these
                    # right after; the artifact stands with partial data if
                    # that fetch fails.
                    source_branch="",
                    target_branch="",
                    author="",
                    provider_created_at=None,
                    provider_updated_at=None,
                    provider_closed_at=None,
                    provider_merged_at=None,
                    merge_commit_sha=None,
                    pr_status=None,
                    checks=None,
                ),
            )

        assert detected.project_path is not None  # guarded by the caller
        assert detected.iid is not None
        return GitLabMergeRequestArtifact(
            id=_artifact_id(self._session_id, external_key),
            session_id=self._session_id,
            external_key=external_key,
            external_url=detected.url,
            display_title=None,
            discovered_at=discovered_at,
            details=GitLabMergeRequestDetails(
                repo_host=detected.host,
                repo_id=detected.repo_id,
                project_path=detected.project_path,
                merge_request_iid=detected.iid,
                state=state,
                source_branch="",
                target_branch="",
                author="",
                provider_created_at=None,
                provider_updated_at=None,
                provider_closed_at=None,
                provider_merged_at=None,
                merge_commit_sha=None,
                pr_status=None,
                checks=None,
            ),
        )

    async def _persist(self) -> None:
        """Write artifacts into ``meta.json`` (read-modify-write).

        Raises on failure — the caller keeps the in-memory artifact, reports
        ``vibe.artifact_detection_failed`` (stage ``persist``) and still
        notifies (best-effort persistence, B9). A missing ``meta.json`` is
        not an error: the session is simply not persisted yet.

        When the adapter shares its metadata lock, the read-modify-write is
        serialized against the adapter's whole-file metadata writes so the
        two never lose each other's updates.
        """
        if not self._dirty:
            return
        async with self._acquire_metadata_lock():
            await self._persist_unlocked()

    async def _persist_unlocked(self) -> None:
        """Persist while the caller already holds ``_metadata_lock`` (if any)."""
        if not self._dirty:
            return
        if self._on_artifacts_changed is not None:
            self._on_artifacts_changed(self.list_artifacts(), self.list_worktrees())
        metadata_path = self._session_dir / METADATA_FILENAME
        if not metadata_path.is_file():
            return
        raw: object = json.loads(read_safe(metadata_path).text)
        if not isinstance(raw, dict):
            raise ValueError(f"Session metadata must be an object: {metadata_path}")
        raw["artifacts"] = [
            artifact.model_dump(mode="json") for artifact in self.list_artifacts()
        ]
        raw["worktrees"] = [
            worktree.model_dump(mode="json") for worktree in self.list_worktrees()
        ]
        await persist_metadata(raw, self._session_dir)
        self._dirty = False


def _has_complete_coordinates(detected: DetectedPullRequest) -> bool:
    """Whether the detected PR/MR carries every field its variant requires."""
    if detected.provider == "github":
        return (
            detected.owner is not None
            and detected.repo is not None
            and detected.number is not None
        )
    return detected.project_path is not None and detected.iid is not None


def _artifact_head(artifact: SessionArtifact) -> PullRequestHead | None:
    details = artifact.details
    if details is None or not details.source_branch:
        return None
    if isinstance(details, GitHubPullRequestDetails):
        repo_path = f"{details.repo_owner}/{details.repo_name}"
    else:
        repo_path = details.project_path
    return (details.repo_host, repo_path, details.source_branch)


def _artifact_is_draft(artifact: SessionArtifact) -> bool:
    return artifact.details is not None and artifact.details.state == "draft"


def _artifact_repo_id_resolved(artifact: SessionArtifact) -> bool:
    """GitLab only — GitHub artifacts have no repo_id concept."""
    if not isinstance(artifact, GitLabMergeRequestArtifact):
        return False
    return artifact.details is not None and artifact.details.repo_id is not None


def _artifact_host_type(
    artifact: SessionArtifact,
) -> Literal["github_com", "gitlab_com", "enterprise"]:
    """Classify the host without leaking its name (telemetry payload)."""
    details = artifact.details
    host = details.repo_host if details is not None else ""
    if artifact.provider == "github" and host == "github.com":
        return "github_com"
    if artifact.provider == "gitlab" and host == "gitlab.com":
        return "gitlab_com"
    return "enterprise"


def _compute_external_key(detected: DetectedPullRequest) -> str:
    if detected.provider == "github":
        assert detected.owner is not None
        assert detected.repo is not None
        assert detected.number is not None
        return _github_external_key(
            detected.host, detected.owner, detected.repo, detected.number
        )
    assert detected.project_path is not None
    assert detected.iid is not None
    return _gitlab_external_key(
        detected.host, detected.repo_id, detected.project_path, detected.iid
    )


# --- Wire projection (Code Web contract shape) ---


def _to_public_checks(checks: ChecksSummary | None) -> PublicChecksSummary | None:
    """Project the storage checks model onto its wire shape — Pydantic does
    not coerce between distinct model classes, so the conversion is explicit.
    """
    if checks is None:
        return None
    return PublicChecksSummary(
        state=checks.state,
        checked_at=checks.checked_at,
        checks=[
            PublicCheckStatus(name=check.name, status=check.status, url=check.url)
            for check in checks.checks
        ],
    )


def _enrichment_fields(
    details: GitHubPullRequestDetails | GitLabMergeRequestDetails,
) -> dict[str, Any]:
    return {
        "source_branch": details.source_branch,
        "target_branch": details.target_branch,
        "author": details.author,
        "state": details.state,
        "provider_created_at": details.provider_created_at,
        "provider_updated_at": details.provider_updated_at,
        "provider_closed_at": details.provider_closed_at,
        "provider_merged_at": details.provider_merged_at,
        "merge_commit_sha": details.merge_commit_sha,
        "pr_status": details.pr_status,
        "pr_status_reason": details.pr_status_reason,
        "checks": _to_public_checks(details.checks),
        "checks_status": details.checks_status,
        "optional_checks_status": details.optional_checks_status,
        "review_decision": details.review_decision,
        "merge_status": details.merge_status,
    }


def _to_public_details(
    artifact: SessionArtifact,
) -> PublicPullRequestArtifactDetails | None:
    details = artifact.details
    if details is None:
        return None
    if artifact.provider == "github":
        assert isinstance(details, GitHubPullRequestDetails)
        return PublicGitHubPullRequestDetails(
            repo_host=details.repo_host,
            repo_owner=details.repo_owner,
            repo_name=details.repo_name,
            pull_request_number=details.pull_request_number,
            **_enrichment_fields(details),
        )
    assert isinstance(details, GitLabMergeRequestDetails)
    return PublicGitLabMergeRequestDetails(
        repo_host=details.repo_host,
        repo_id=details.repo_id,
        project_path=details.project_path,
        merge_request_iid=details.merge_request_iid,
        **_enrichment_fields(details),
    )


def to_public_artifact(artifact: SessionArtifact) -> PublicSessionArtifact:
    """Project a stored artifact onto its wire (camelCase) form, with its
    status as last read from its provider (:func:`with_cached_status`).
    """
    artifact = with_cached_status(artifact)
    return PublicSessionArtifact(
        id=artifact.id,
        session_id=artifact.session_id,
        provider=artifact.provider,
        external_key=artifact.external_key,
        external_url=artifact.external_url,
        display_title=artifact.display_title,
        discovered_at=artifact.discovered_at,
        viewed_at=artifact.viewed_at,
        details=_to_public_details(artifact),
    )


def to_public_worktree(worktree: WorktreeArtifact) -> PublicWorktreeArtifact:
    # Kept apart from the stored model like PRs are: the wire is camelCase and
    # must not change when meta.json does.
    return PublicWorktreeArtifact.model_validate(worktree.model_dump())


class ArtifactRequestHandler:
    """Serves the ``artifacts/`` RPC namespace for one session.

    Mirrors the ``review/`` handler shape: constructed by the session adapter
    with its dependencies, dispatched per method. The service answers from the
    persisted artifacts whether PR monitoring is on or off; the flag gates
    detection and provider reads. ``artifacts/sync`` is the host's
    (``SessionBackendHostArtifacts.sync_artifacts``).

    ``emit_updated`` announces each artifact a ``markViewed`` call newly marks
    (the ``artifacts/updated`` notification); the adapter supplies it because
    only the adapter owns the notification surface. When it is ``None`` the
    marking still happens, the updates are just not announced.
    """

    def __init__(
        self,
        artifact_service: SessionArtifactService | None,
        require_session: Callable[[str], None],
        *,
        emit_updated: Callable[[SessionArtifact], Awaitable[None]] | None = None,
    ) -> None:
        self._artifact_service = artifact_service
        self._require_session = require_session
        self._emit_updated = emit_updated

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        match method:
            case "artifacts/list":
                params = validate_wire(ArtifactsListParams, raw_params)
                self._require_session(params.session_id)
                artifacts = (
                    self._artifact_service.list_artifacts()
                    if self._artifact_service is not None
                    else []
                )
                worktrees = (
                    self._artifact_service.list_worktrees()
                    if self._artifact_service is not None
                    else []
                )
                response: ProtocolModel = ArtifactsListResponse(
                    artifacts=[to_public_artifact(a) for a in artifacts],
                    worktrees=[to_public_worktree(w) for w in worktrees],
                )
            case "artifacts/markViewed":
                params = validate_wire(ArtifactsMarkViewedParams, raw_params)
                self._require_session(params.session_id)
                marked = (
                    await self._artifact_service.mark_viewed(params.artifact_ids)
                    if self._artifact_service is not None
                    else []
                )
                if self._emit_updated is not None:
                    for artifact in marked:
                        await self._emit_updated(artifact)
                response = EmptyResponse()
            case _:
                raise method_not_found(method)
        return DispatchResult(response)
