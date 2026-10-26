"""Detect PR/MR creation from shell commands and output.

Intercepts ``gh pr create`` (GitHub) and ``glab mr create`` (GitLab) to extract
the created PR/MR coordinates. Supports GitHub Enterprise and self-hosted GitLab
by capturing the host from the output URL.

CLI binary resolution (``gh`` / ``glab``) uses a two-tier approach:
A — system-installed binary (``shutil.which``), prioritized because it may be
    newer and already authenticated.
C — binary bundled in the PyInstaller distribution (``VIBE_ROOT/bin/``),
    used as a fallback when no system binary is found.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import re
import shutil
import sys
from typing import Literal
from urllib.parse import quote

from vibe import VIBE_ROOT

__all__ = [
    "DetectedPullRequest",
    "cli_binary_source",
    "extract_draft_from_command",
    "extract_pr_from_output",
    "extract_title_from_command",
    "find_cli_binary",
    "is_pr_creation_command",
    "resolve_gitlab_repo_id",
]


@dataclass(frozen=True, slots=True)
class DetectedPullRequest:
    """Coordinates of a PR/MR extracted from a shell command's output."""

    provider: Literal["github", "gitlab"]
    url: str
    host: str
    # GitHub
    owner: str | None = None
    repo: str | None = None
    number: int | None = None
    # GitLab
    project_path: str | None = None
    iid: int | None = None
    repo_id: int | None = None  # Resolved via glab api if available
    # Common
    title: str | None = None
    draft: bool = False


# --- CLI binary resolution (Approach A + C) ---


def find_cli_binary(name: str) -> str | None:
    """Resolve a CLI binary (``gh``, ``glab``) — approach A + C.

    Resolution order:
    1. **A — System binary**: ``shutil.which(name)`` — binary installed by the
       user (Homebrew, apt, etc.). Prioritized because it may be newer and
       already authenticated.
    2. **C — Bundled binary**: ``VIBE_ROOT / "bin" / name[.exe]`` — binary
       shipped in the PyInstaller bundle (onedir → ``_internal/vibe/bin/``).
       Guarantees ``gh``/``glab`` are available in the standalone distribution
       even without a prior installation.

    Returns ``None`` if neither is found (e.g. dev mode without bundled binary
    and without ``gh``/``glab`` installed).
    """
    # A — system
    system_path = shutil.which(name)
    if system_path is not None:
        return system_path

    # C — bundled in the PyInstaller bundle
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    bundled = VIBE_ROOT / "bin" / exe_name
    if bundled.is_file():
        return str(bundled)

    return None


def cli_binary_source(name: str) -> Literal["system", "bundled", "none"]:
    """Classify how a CLI binary would be resolved (for telemetry)."""
    if shutil.which(name) is not None:
        return "system"
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    if (VIBE_ROOT / "bin" / exe_name).is_file():
        return "bundled"
    return "none"


# --- Command detection ---

# Quoted spans (single or double quotes, backslash escapes allowed) are
# blanked before matching: ``rg 'gh pr create'`` is a search, not a creation.
_QUOTED_SPAN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')

# A creation must start a command position: the beginning of the command
# string, or right after a shell separator. This keeps ``echo gh pr create``
# (a message, not a creation) from matching.
_COMMAND_POSITION = r"(?:^|[;&|(\n])\s*"

# Global flags may sit between the CLI and its subcommand (``gh -R o/r pr
# create``, ``glab --hostname gitlab.example.com mr create``), and the binary
# may be path-qualified (``/usr/local/bin/gh pr create``).
_GLOBAL_FLAGS = r"(?:\s+(?:-R|--repo|--hostname)(?:[=\s]\S+)?)*"


def _cli_command_re(cli: str, subcommand: str) -> re.Pattern[str]:
    return re.compile(
        _COMMAND_POSITION
        + rf"(?:[\w.@~+/:\\-]+[\\/])?{cli}(?:\.exe)?"
        + _GLOBAL_FLAGS
        + rf"\s+{subcommand}\b",
        re.IGNORECASE,
    )


_GH_PR_CREATE_RE = _cli_command_re("gh", r"pr\s+create")
_GLAB_MR_CREATE_RE = _cli_command_re("glab", r"mr\s+create")
# Reopening a closed PR/MR makes it the session's PR again, exactly like
# creating one.
_GH_PR_REOPEN_RE = _cli_command_re("gh", r"pr\s+reopen")
_GLAB_MR_REOPEN_RE = _cli_command_re("glab", r"mr\s+reopen")


def is_pr_creation_command(command: str) -> Literal["github", "gitlab"] | None:
    """Return the provider if the command creates or reopens a PR/MR, else None."""
    unquoted = _QUOTED_SPAN_RE.sub(" ", command)
    if _GH_PR_CREATE_RE.search(unquoted) or _GH_PR_REOPEN_RE.search(unquoted):
        return "github"
    if _GLAB_MR_CREATE_RE.search(unquoted) or _GLAB_MR_REOPEN_RE.search(unquoted):
        return "gitlab"
    return None


# --- Output extraction ---

# Capture the host to support GitHub Enterprise (github.mycompany.com).
_GH_PR_URL_RE = re.compile(r"https?://([^/\s]+)/([^/\s]+)/([^/\s]+)/pull/(\d+)")
_GITLAB_MR_URL_RE = re.compile(r"https?://([^/\s]+)/(.+?)/-/merge_requests/(\d+)")

# Reopen outputs carry no URL: ``gh pr reopen`` prints
# "✓ Reopened pull request owner/repo#123 (title)". The host is not in the
# output; the caller resolves it from the session's checkout remote.
_GH_REOPEN_FULL_RE = re.compile(
    r"[Rr]eopened\s+pull\s+request\s+([\w.-]+)/([\w.-]+)#(\d+)"
)
# Shorter gh output forms (older versions, quiet mode):
# "✓ Reopened pull request #123" and "reopened #123".
_GH_REOPEN_NUMBER_RE = re.compile(r"[Rr]eopened\s+(?:pull\s+request\s+)?#(\d+)")
# ``glab mr reopen`` prints a "reopened" confirmation with the !iid.
_GLAB_REOPEN_IID_RE = re.compile(r"[Rr]eopened\s+(?:merge\s+request\s+)?!?(\d+)")


def extract_pr_from_output(
    stdout: str, stderr: str, provider: Literal["github", "gitlab"] | None = None
) -> DetectedPullRequest | None:
    """Extract PR/MR coordinates from command output (stdout + stderr).

    For a *reopened* PR/MR the output carries no URL, so the coordinates may
    be partial (``number``/``iid`` only, or without the host): the caller
    resolves the missing fields from the session's checkout remote.

    When *provider* is given (the provider already detected from the command),
    its URL and reopen-confirmation patterns are tried first, so an output
    that also mentions the other provider's PR/MR — an MR description linking
    a GitHub PR, a compound command — is not misrecorded. The other provider's
    patterns remain as a fallback so a provider whose URL format the regexes
    miss is not lost entirely.
    """
    text = f"{stdout}\n{stderr}"

    def _github_url() -> DetectedPullRequest | None:
        # GitHub (github.com or GitHub Enterprise — host captured from the URL)
        match = _GH_PR_URL_RE.search(text)
        if match:
            host, owner, repo, number = match.groups()
            return DetectedPullRequest(
                provider="github",
                url=match.group(0),
                host=host,
                owner=owner,
                repo=repo,
                number=int(number),
            )
        return None

    def _gitlab_url() -> DetectedPullRequest | None:
        # GitLab (gitlab.com or self-hosted — host captured from the URL)
        match = _GITLAB_MR_URL_RE.search(text)
        if match:
            host, project_path, iid = match.groups()
            return DetectedPullRequest(
                provider="gitlab",
                url=match.group(0),
                host=host,
                project_path=project_path,
                iid=int(iid),
                repo_id=None,  # Resolved by resolve_gitlab_repo_id() if glab is available
            )
        return None

    def _github_reopen() -> DetectedPullRequest | None:
        # Reopen confirmations — no URL. Coordinates may be partial; the
        # caller fills the missing fields from the checkout remote.
        match = _GH_REOPEN_FULL_RE.search(text)
        if match:
            owner, repo, number = match.groups()
            return DetectedPullRequest(
                provider="github",
                url="",
                host="",  # Resolved from the checkout remote
                owner=owner,
                repo=repo,
                number=int(number),
            )
        match = _GH_REOPEN_NUMBER_RE.search(text)
        if match:
            return DetectedPullRequest(
                provider="github", url="", host="", number=int(match.group(1))
            )
        return None

    def _gitlab_reopen() -> DetectedPullRequest | None:
        match = _GLAB_REOPEN_IID_RE.search(text)
        if match:
            return DetectedPullRequest(
                provider="gitlab", url="", host="", iid=int(match.group(1))
            )
        return None

    if provider == "github":
        extractors = (_github_url, _github_reopen, _gitlab_url, _gitlab_reopen)
    elif provider == "gitlab":
        extractors = (_gitlab_url, _gitlab_reopen, _github_url, _github_reopen)
    else:
        extractors = (_github_url, _gitlab_url, _github_reopen, _gitlab_reopen)
    for extract in extractors:
        detected = extract()
        if detected is not None:
            return detected
    return None


# --- GitLab repo_id resolution via glab api ---


async def resolve_gitlab_repo_id(
    host: str, project_path: str, *, timeout: float = 10.0
) -> int | None:
    """Resolve the numeric GitLab repo_id via ``glab api`` (best-effort).

    Returns None if glab is not installed, not authenticated, or the command
    fails. The caller falls back to using ``project_path`` in the external key.
    """
    glab = find_cli_binary("glab")
    if glab is None:
        return None

    encoded_path = quote(project_path, safe="")
    cmd = [glab, "api", f"projects/{encoded_path}"]

    # glab respects GITLAB_HOST for self-hosted instances; pass it through
    # when the host is not gitlab.com.
    env = dict(os.environ)
    if host != "gitlab.com":
        env["GITLAB_HOST"] = host

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            # Never inherit stdin: on the stdio transport the child could
            # consume RPC bytes or block on a prompt until the timeout.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        except asyncio.CancelledError:
            # The drain was cancelled (session close, shutdown): kill the
            # child so it does not outlive the session with open pipes.
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return None

        data = json.loads(stdout.decode("utf-8"))
        repo_id = data.get("id")
        if isinstance(repo_id, int) and repo_id > 0:
            return repo_id
        return None

    except (json.JSONDecodeError, OSError, KeyError):
        return None


# --- Metadata extraction from the command ---


def _mask_quoted_spans(command: str) -> str:
    """Blank quoted spans with same-length NUL placeholders, so match
    positions in the masked command align 1:1 with the raw command and
    whitespace-separator patterns stop right before a blanked value.
    """
    return _QUOTED_SPAN_RE.sub(lambda match: "\x00" * len(match.group(0)), command)


# Title flag locators run on the masked command: a ``--title`` / ``-t``
# inside a quoted ``--body``/``--description`` is text, not a flag. The
# title's own quoted argument is read back from the raw command.
_TITLE_FLAG_PATTERNS = [
    re.compile(r"--title[=\s]+"),
    # ``-t`` must start a token so it never matches inside ``--title`` or a
    # longer flag.
    re.compile(r"(?<![\w-])-t\s+"),
]
_TITLE_VALUE_PATTERNS = [
    # A quoted title may contain spaces — capture everything between quotes.
    re.compile(r"[\"']([^\"']+)[\"']"),
    # An unquoted title is a single shell token — stop before the next flag.
    re.compile(r"([^\"\'\s]\S*)"),
]

# --draft is unambiguous for both gh and glab.
_DRAFT_LONG_RE = re.compile(r"--draft\b")
# -d means --draft for gh but --description for glab; only match it for gh.
_DRAFT_SHORT_GH_RE = re.compile(r"(?<![\w-])-d\b")


def extract_title_from_command(command: str) -> str | None:
    """Extract the PR/MR title from ``--title`` / ``-t`` in the command."""
    masked = _mask_quoted_spans(command)
    for flag_pattern in _TITLE_FLAG_PATTERNS:
        flag_match = flag_pattern.search(masked)
        if flag_match is None:
            continue
        rest = command[flag_match.end() :]
        for value_pattern in _TITLE_VALUE_PATTERNS:
            value_match = value_pattern.match(rest)
            if value_match:
                return value_match.group(1).strip()
    return None


def extract_draft_from_command(
    command: str, provider: Literal["github", "gitlab"]
) -> bool:
    """Extract the draft flag from the command.

    ``--draft`` is unambiguous for both providers. ``-d`` means ``--draft`` for
    ``gh`` but ``--description`` for ``glab``, so it is only matched for GitHub.
    Quoted spans are blanked first: a ``--draft`` / ``-d`` inside a quoted
    ``--title``/``--body`` is text, not a flag.
    """
    unquoted = _QUOTED_SPAN_RE.sub(" ", command)
    if _DRAFT_LONG_RE.search(unquoted):
        return True
    if provider == "github" and _DRAFT_SHORT_GH_RE.search(unquoted):
        return True
    return False
