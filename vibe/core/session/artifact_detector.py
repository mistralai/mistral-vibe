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
from collections.abc import Iterator
from dataclasses import dataclass
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Literal
from urllib.parse import quote

from vibe import VIBE_ROOT
from vibe.core.utils.bash_parser import bash_parser, walk

__all__ = [
    "DetectedPullRequest",
    "cli_binary_source",
    "extract_draft_from_command",
    "extract_prs_from_output",
    "find_cli_binary",
    "is_pr_creation_command",
    "pr_creation_limit",
    "pr_creation_output_first",
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


def _shell_code_only(command: str) -> str:
    """Blank what the shell reads as data: heredoc bodies, then quoted spans.

    A heredoc body goes first, so an apostrophe in a PR body cannot open a
    quoted span that swallows the ``gh pr create`` after it. A ``$(...)`` in
    the body is code the shell runs, so it stays. A body the grammar could not
    read, inside an ``ERROR`` node, stays too, as it did before parsing.
    """
    source = command.encode("utf-8")
    root = bash_parser().parse(source).root_node
    blanked = bytearray(source)
    for body in walk(root, skip={"ERROR"}):
        if body.type != "heredoc_body":
            continue
        start = body.start_byte
        for code in walk(body):
            if code.type == "command_substitution" and code.start_byte >= start:
                _blank(blanked, start, code.start_byte)
                start = code.end_byte
        _blank(blanked, start, body.end_byte)
    return _QUOTED_SPAN_RE.sub(" ", blanked.decode("utf-8"))


def _blank(source: bytearray, start: int, end: int) -> None:
    source[start:end] = re.sub(rb"[^\n]", b" ", bytes(source[start:end]))


_COMMENT_RE = re.compile(r"(?:^|(?<=\s))#[^\n]*")
_COMMAND_SEPARATOR_RE = re.compile(r"[;&|(\n]")

# The CLI may sit anywhere in a simple command, after a wrapper that runs it
# (``perl -e 'alarm 90; exec @ARGV' gh``, ``timeout 90 gh``, ``env X=1 gh``),
# but only as a whole word: ``foo-gh`` and ``gh.sh`` are other programs.
# Commands that print their arguments say nothing about a creation.
_PRINTING_COMMANDS = frozenset({"echo", "printf", "print"})
# Commands that run their arguments once per input line, like a loop body.
_REPEATING_COMMANDS = frozenset({"xargs", "parallel"})

# Global flags may sit between the CLI and its subcommand (``gh -R o/r pr
# create``, ``glab --hostname gitlab.example.com mr create``), and the binary
# may be path-qualified (``/usr/local/bin/gh pr create``).
_GLOBAL_FLAGS = r"(?:\s+(?:-R|--repo|--hostname)(?:[=\s]\S+)?)*"


def _cli_command_re(cli: str, subcommand: str) -> re.Pattern[str]:
    return re.compile(
        r"(?:^|(?<=\s))"
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
# The gh/glab subcommands that may print PR/MR URLs.
_GH_URL_OUTPUT_RE = _cli_command_re("gh", r"(?:pr|api|search)")
_GLAB_URL_OUTPUT_RE = _cli_command_re("glab", r"(?:mr|api)")


def _segments(command: str) -> list[str]:
    """The simple commands of *command*, heredocs, quoted spans and comments blanked."""
    unquoted = _COMMENT_RE.sub(" ", _shell_code_only(command))
    return _COMMAND_SEPARATOR_RE.split(unquoted)


def _segment_provider(segment: str) -> Literal["github", "gitlab"] | None:
    """The provider when *segment* creates or reopens a PR/MR."""
    words = segment.split(maxsplit=1)
    if not words or words[0].lower() in _PRINTING_COMMANDS:
        return None
    if _GH_PR_CREATE_RE.search(segment) or _GH_PR_REOPEN_RE.search(segment):
        return "github"
    if _GLAB_MR_CREATE_RE.search(segment) or _GLAB_MR_REOPEN_RE.search(segment):
        return "gitlab"
    return None


def _prints_pr_urls(segment: str) -> bool:
    """Whether *segment* runs a gh/glab subcommand that may print PR/MR URLs."""
    words = segment.split(maxsplit=1)
    if not words or words[0].lower() in _PRINTING_COMMANDS:
        return False
    return bool(
        _GH_URL_OUTPUT_RE.search(segment) or _GLAB_URL_OUTPUT_RE.search(segment)
    )


def _creation_segments(
    command: str,
) -> Iterator[tuple[Literal["github", "gitlab"], bool]]:
    """Each create/reopen segment's provider, and whether it may run repeatedly."""
    loop_depth = 0
    for segment in _segments(command):
        words = segment.split(maxsplit=1)
        if not words:
            continue
        first = words[0].lower()
        if first == "do":
            loop_depth += 1
        elif first == "done":
            loop_depth = max(0, loop_depth - 1)
        if (provider := _segment_provider(segment)) is not None:
            yield provider, loop_depth > 0 or first in _REPEATING_COMMANDS


def is_pr_creation_command(command: str) -> Literal["github", "gitlab"] | None:
    """Return the provider if the command creates or reopens a PR/MR, else None."""
    for provider, _repeated in _creation_segments(command):
        return provider
    return None


def pr_creation_limit(command: str) -> int | None:
    """How many PRs/MRs the command may create or reopen; None when unbounded."""
    count = 0
    for _provider, repeated in _creation_segments(command):
        # A loop body (or xargs) may create any number of PRs/MRs.
        if repeated:
            return None
        count += 1
    return count


def pr_creation_output_first(command: str) -> bool:
    """Whether the created PRs/MRs print their URLs before any other gh/glab output.

    True when gh/glab runs only after the creations (``gh pr create && gh pr
    view 9``): the first URLs are then the created ones, otherwise the last.
    """
    created = False
    later_output = False
    for segment in _segments(command):
        if _segment_provider(segment) is not None:
            created = True
        elif _prints_pr_urls(segment):
            if not created:
                return False
            later_output = True
    return later_output


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


def extract_prs_from_output(
    stdout: str,
    stderr: str,
    provider: Literal["github", "gitlab"] | None = None,
    limit: int | None = None,
    from_start: bool = False,
) -> list[DetectedPullRequest]:
    """Extract PR/MR coordinates from command output (stdout + stderr).

    Returns every distinct PR/MR URL of the first matching provider, so a
    loop creating several PRs records them all. With *limit* (see
    ``pr_creation_limit``), only the last *limit* distinct URLs are kept:
    ``gh pr list ... && gh pr create`` records the created PR, not the listed ones.
    With *from_start* (see ``pr_creation_output_first``), the first ones are.

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

    def _distinct(pattern: re.Pattern[str]) -> list[re.Match[str]]:
        matches = list(pattern.finditer(text))
        # A creation prints its URL after any earlier command's output.
        newest_first = limit is not None and not from_start
        if newest_first:
            matches.reverse()
        found: dict[str, re.Match[str]] = {}
        for match in matches:
            found.setdefault(match.group(0), match)
        distinct = list(found.values())
        if limit is None:
            return distinct
        return distinct[:limit][::-1] if newest_first else distinct[:limit]

    def _github_urls() -> list[DetectedPullRequest]:
        # GitHub (github.com or GitHub Enterprise — host captured from the URL)
        found: list[DetectedPullRequest] = []
        for match in _distinct(_GH_PR_URL_RE):
            host, owner, repo, number = match.groups()
            found.append(
                DetectedPullRequest(
                    provider="github",
                    url=match.group(0),
                    host=host,
                    owner=owner,
                    repo=repo,
                    number=int(number),
                )
            )
        return found

    def _gitlab_urls() -> list[DetectedPullRequest]:
        # GitLab (gitlab.com or self-hosted — host captured from the URL)
        found: list[DetectedPullRequest] = []
        for match in _distinct(_GITLAB_MR_URL_RE):
            host, project_path, iid = match.groups()
            found.append(
                DetectedPullRequest(
                    provider="gitlab",
                    url=match.group(0),
                    host=host,
                    project_path=project_path,
                    iid=int(iid),
                    repo_id=None,  # Resolved by resolve_gitlab_repo_id() if glab is available
                )
            )
        return found

    def _github_reopen() -> list[DetectedPullRequest]:
        # Reopen confirmations — no URL. Coordinates may be partial; the
        # caller fills the missing fields from the checkout remote.
        full = {
            match.groups(): DetectedPullRequest(
                provider="github",
                url="",
                host="",  # Resolved from the checkout remote
                owner=match.group(1),
                repo=match.group(2),
                number=int(match.group(3)),
            )
            for match in _GH_REOPEN_FULL_RE.finditer(text)
        }
        if full:
            return list(full.values())
        numbers = dict.fromkeys(
            int(match.group(1)) for match in _GH_REOPEN_NUMBER_RE.finditer(text)
        )
        return [
            DetectedPullRequest(provider="github", url="", host="", number=number)
            for number in numbers
        ]

    def _gitlab_reopen() -> list[DetectedPullRequest]:
        iids = dict.fromkeys(
            int(match.group(1)) for match in _GLAB_REOPEN_IID_RE.finditer(text)
        )
        return [
            DetectedPullRequest(provider="gitlab", url="", host="", iid=iid)
            for iid in iids
        ]

    if provider == "github":
        extractors = (_github_urls, _github_reopen, _gitlab_urls, _gitlab_reopen)
    elif provider == "gitlab":
        extractors = (_gitlab_urls, _gitlab_reopen, _github_urls, _github_reopen)
    else:
        extractors = (_github_urls, _gitlab_urls, _github_reopen, _gitlab_reopen)
    for extract in extractors:
        if detected := extract():
            return detected
    return []


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
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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


# --draft is unambiguous for both gh and glab.
_DRAFT_LONG_RE = re.compile(r"--draft\b")
# -d means --draft for gh but --description for glab; only match it for gh.
_DRAFT_SHORT_GH_RE = re.compile(r"(?<![\w-])-d\b")


def extract_draft_from_command(
    command: str, provider: Literal["github", "gitlab"]
) -> bool:
    """Extract the draft flag from the command.

    ``--draft`` is unambiguous for both providers. ``-d`` means ``--draft`` for
    ``gh`` but ``--description`` for ``glab``, so it is only matched for GitHub.
    Quoted spans and heredoc bodies are blanked first: a ``--draft`` / ``-d``
    inside a quoted ``--title``/``--body`` or a heredoc is text, not a flag.
    With several create/reopen segments, a URL cannot be matched to its
    segment: draft only when every segment carries the flag.
    """
    segments = [s for s in _segments(command) if _segment_provider(s) is not None]
    if len(segments) <= 1:
        segments = [_shell_code_only(command)]

    def is_draft(segment: str) -> bool:
        if _DRAFT_LONG_RE.search(segment):
            return True
        return provider == "github" and bool(_DRAFT_SHORT_GH_RE.search(segment))

    return all(is_draft(segment) for segment in segments)
