"""Forge discovery — find open PRs/MRs for the checkout's current branch.

Phase 2 of session artifacts: when a session loads on a local checkout with no
existing PR artifacts, this module runs ``gh pr list --head <branch>`` /
``glab mr list --source-branch <branch>`` to discover the pull requests and
merge requests that are linked to the branch the session is on. Each result
becomes a ``SessionArtifact`` (same model as Phase 1 detection), persisted and
notified through the existing ``artifacts/created`` channel.

Discovery is branch-scoped on purpose: a repository can have hundreds of open
PRs, and only the ones whose head branch is the session's branch are relevant
to the session. Local only. No cloud-sandbox support in Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Literal

from giturlparse import parse as parse_git_url

from vibe.core.git.errors import GitError, GitRepositoryNotFoundError
from vibe.core.git.remote import parse_github_url
from vibe.core.git.worktree.repository import WorktreeRepository
from vibe.core.session.artifact_detector import find_cli_binary
from vibe.observability.logging import logger

__all__ = ["DiscoveredPullRequest", "ForgeError", "discover_pull_requests"]

# Generous enough that a slow forge answer is not mistaken for a hang; the
# caller treats a timeout as "no discovery" and moves on.
_LIST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class DiscoveredPullRequest:
    """An open PR/MR found via ``gh pr list`` / ``glab mr list``."""

    provider: Literal["github", "gitlab"]
    number: int
    title: str
    url: str
    source_branch: str
    target_branch: str
    state: Literal["draft", "open"]
    author: str
    repo_host: str
    # GitHub: owner login. GitLab: project path (e.g. "org/subgroup/repo").
    repo_owner: str
    repo_name: str


class ForgeError(Exception):
    """Discovery failed — no CLI, no auth, no remote, or command error."""


def _detect_provider(repo_url: str) -> Literal["github", "gitlab"] | None:
    """Classify a normalised remote URL as github or gitlab."""
    if parse_github_url(repo_url) is not None:
        return "github"
    # giturlparse handles gitlab.com and self-hosted GitLab; the host alone
    # does not prove it, but combined with the URL shape it is enough for
    # Phase 2 (GitHub is the common case; GitLab subgroups parse correctly).
    parsed = parse_git_url(repo_url)
    if parsed.valid and parsed.host and parsed.owner and parsed.repo:
        host = parsed.host.lower()
        if "gitlab" in host:
            return "gitlab"
    return None


def _run_gh_pr_list(
    gh_path: str, owner: str, repo: str, host: str, branch: str
) -> list[DiscoveredPullRequest]:
    """Run ``gh pr list --state open --head <branch> --json`` and parse."""
    cmd = [
        gh_path,
        "pr",
        "list",
        "--state",
        "open",
        "--repo",
        f"{owner}/{repo}",
        "--head",
        branch,
        "--json",
        "number,title,url,headRefName,baseRefName,isDraft,author",
        "--limit",
        "100",
    ]

    # Full environment: gh reads its token and hosts config from env/HOME.
    # GH_HOST is pinned to the checkout's remote host so an inherited value
    # (e.g. a shell configured for a GHES instance) cannot steer discovery at
    # a different forge than the one the session's remote points to.
    # UTF-8 explicitly: PR titles are not ASCII, and the locale codepage must
    # not decide how the CLI's output is decoded.
    env = dict(os.environ)
    env["GH_HOST"] = host
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_LIST_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ForgeError(
            f"gh pr list timed out after {_LIST_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise ForgeError(f"gh pr list could not be executed: {exc}") from exc
    if result.returncode != 0:
        raise ForgeError(
            f"gh pr list failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    try:
        raw_list: list[dict[str, object]] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ForgeError(f"gh pr list returned invalid JSON: {exc}") from exc

    prs: list[DiscoveredPullRequest] = []
    for item in raw_list:
        number = item.get("number")
        title = item.get("title")
        url = item.get("url")
        head = item.get("headRefName")
        base = item.get("baseRefName")
        is_draft = item.get("isDraft")
        author_obj = item.get("author")
        author = author_obj.get("login", "") if isinstance(author_obj, dict) else ""

        if not all(isinstance(v, (int, str)) for v in (number, title, url, head, base)):
            continue

        prs.append(
            DiscoveredPullRequest(
                provider="github",
                number=int(number),  # type: ignore[arg-type]
                title=str(title),
                url=str(url),
                source_branch=str(head),
                target_branch=str(base),
                state="draft" if is_draft else "open",
                author=author,
                repo_host=host,
                repo_owner=owner,
                repo_name=repo,
            )
        )
    return prs


def _run_glab_mr_list(
    glab_path: str, project_path: str, host: str, branch: str
) -> list[DiscoveredPullRequest]:
    """Run ``glab mr list --source-branch <branch>`` and parse.

    ``glab mr list`` lists opened MRs by default; the deprecated ``--opened``
    flag is intentionally omitted.
    """
    cmd = [
        glab_path,
        "mr",
        "list",
        "--repo",
        project_path,
        "--source-branch",
        branch,
        "--output",
        "json",
        "--per-page",
        "100",
    ]
    # glab respects GITLAB_HOST for the instance (same as the Phase 1 repo_id
    # resolver); it is pinned to the checkout's remote host — including
    # gitlab.com — so an inherited value cannot steer discovery at a different
    # instance. The full environment carries the token and config.
    env = dict(os.environ)
    env["GITLAB_HOST"] = host

    # UTF-8 explicitly: MR titles are not ASCII, and the locale codepage must
    # not decide how the CLI's output is decoded.
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_LIST_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ForgeError(
            f"glab mr list timed out after {_LIST_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise ForgeError(f"glab mr list could not be executed: {exc}") from exc
    if result.returncode != 0:
        raise ForgeError(
            f"glab mr list failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    try:
        raw_list: list[dict[str, object]] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ForgeError(f"glab mr list returned invalid JSON: {exc}") from exc

    prs: list[DiscoveredPullRequest] = []
    for item in raw_list:
        iid = item.get("iid")
        title = item.get("title")
        web_url = item.get("web_url")
        source_branch = item.get("source_branch")
        target_branch = item.get("target_branch")
        draft = item.get("draft", False)
        author_obj = item.get("author")
        author = author_obj.get("username", "") if isinstance(author_obj, dict) else ""

        if not all(
            isinstance(v, (int, str))
            for v in (iid, title, web_url, source_branch, target_branch)
        ):
            continue

        # GitLab project path: "org/subgroup/repo" — owner is the full path,
        # repo_name is the last segment.
        path_parts = project_path.split("/")
        repo_name = path_parts[-1] if path_parts else project_path

        prs.append(
            DiscoveredPullRequest(
                provider="gitlab",
                number=int(iid),  # type: ignore[arg-type]
                title=str(title),
                url=str(web_url),
                source_branch=str(source_branch),
                target_branch=str(target_branch),
                state="draft" if draft else "open",
                author=author,
                repo_host=host,
                repo_owner=project_path,
                repo_name=repo_name,
            )
        )
    return prs


def discover_pull_requests(repo_path: Path) -> list[DiscoveredPullRequest]:
    """Discover open PRs/MRs linked to the checkout's current branch.

    Opens the git repository at *repo_path*, reads its remote and current
    branch, detects the forge provider (GitHub or GitLab), resolves the CLI
    binary, and runs ``gh pr list --head <branch>`` / ``glab mr list
    --source-branch <branch>`` to enumerate the open PRs/MRs whose head
    branch is the session's branch. Draft PRs are included — a draft is
    still the PR the session's branch is about.

    Raises ``ForgeError`` when discovery cannot proceed (no CLI, no auth,
    no remote, unsupported forge, command failure). Returns an empty list
    when the branch has no open PRs/MRs or the checkout is on a detached
    HEAD (no current branch to scope to).

    Synchronous: callers run this in a thread (``asyncio.to_thread``).
    """
    try:
        with WorktreeRepository.open(repo_path) as repo:
            status = repo.status()
            remote_url = status.repo_url
            if remote_url is None:
                raise ForgeError("No git remote found for this checkout")
            branch = status.branch
            if branch is None:
                # Detached HEAD: there is no current branch, hence no PR/MR
                # can be linked to it. Not an error — nothing to discover.
                return []

            provider = _detect_provider(remote_url)
            if provider is None:
                raise ForgeError(f"Unsupported forge for remote: {remote_url}")

            if provider == "github":
                parsed = parse_github_url(remote_url)
                if parsed is None:
                    raise ForgeError(f"Could not parse GitHub remote: {remote_url}")
                owner, repo_name = parsed
                # Extract host from the normalised URL.
                url_parsed = parse_git_url(remote_url)
                host = url_parsed.host if url_parsed.host else "github.com"

                gh_path = find_cli_binary("gh")
                if gh_path is None:
                    raise ForgeError("gh CLI not found (system or bundled)")
                logger.debug(
                    "Discovering GitHub PRs",
                    extra={
                        "owner": owner,
                        "repo": repo_name,
                        "host": host,
                        "branch": branch,
                    },
                )
                return _run_gh_pr_list(gh_path, owner, repo_name, host, branch)

            # GitLab
            url_parsed = parse_git_url(remote_url)
            host = url_parsed.host if url_parsed.host else "gitlab.com"
            # project_path = owner/groups/repo
            path_parts = [
                p for p in (url_parsed.owner, *url_parsed.groups, url_parsed.repo) if p
            ]
            project_path = "/".join(path_parts)

            glab_path = find_cli_binary("glab")
            if glab_path is None:
                raise ForgeError("glab CLI not found (system or bundled)")
            logger.debug(
                "Discovering GitLab MRs",
                extra={"project_path": project_path, "host": host, "branch": branch},
            )
            return _run_glab_mr_list(glab_path, project_path, host, branch)

    except GitRepositoryNotFoundError as exc:
        raise ForgeError(f"Not a git repository: {repo_path}") from exc
    except GitError as exc:
        raise ForgeError(f"Git error: {exc}") from exc
