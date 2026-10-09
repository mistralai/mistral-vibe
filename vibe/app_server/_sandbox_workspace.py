"""Workspace context read through a Sandbox Adapter instead of the host.

When a session's tools run in a sandbox, the files and the repository the model
works on live there, not on the host. The project context (absolute path and
git metadata) and the AGENTS.md docs the host would read from its own disk are
read through the adapter instead, and rendered exactly as the host renders
them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import TYPE_CHECKING

from mistralai_vibe_local_harness.vibe import (
    SANDBOX_FAILURES,
    SandboxUnavailableError,
    sandbox_path,
)
from vibe.core.paths.conventions import AGENTS_MD_FILENAME
from vibe.core.system_prompt import (
    GIT_TIMEOUT_MESSAGE,
    GIT_UNAVAILABLE_MESSAGE,
    format_git_context,
    git_metadata_args,
    render_project_context,
)
from vibe.observability.logging import logger
from vibe.utils.io import decode_safe

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import SandboxAdapter
    from vibe.core.config import ProjectContextConfig

AGENTS_MD_MAX_BYTES = 1024 * 1024
_GIT_TIMEOUT_CAP_SECONDS = 10.0
# Where a trusted workspace keeps the project sources the host loads: config,
# agents, skills, tools, hooks, plugins and prompts. Only its skills are read
# from a sandbox.
_PROJECT_DIR = ".vibe"
_READ_PROJECT_SOURCES = (".vibe/skills",)
_PROJECT_SOURCES_TIMEOUT_SECONDS = 10.0


class _GitCommandFailedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SandboxWorkspaceContext:
    """What a session's system instructions take from its sandbox workspace."""

    project_context: str
    project_docs: list[tuple[Path, str]]


async def read_sandbox_workspace_context(
    sandbox: SandboxAdapter,
    config: ProjectContextConfig,
    *,
    cwd: Path,
    roots: Sequence[Path],
) -> SandboxWorkspaceContext:
    """Read the project context and the open roots' AGENTS.md docs.

    ``roots`` are the session's open directories, the cwd first. The sandbox
    has no trust store: its workspace stands for the host's trust root, so the
    cwd's docs are read from the cwd up to the workspace when it lies inside
    it, as the host reads them up to the trusted folder. Every other root
    contributes its own AGENTS.md only, as an added directory does on the host.
    """
    trust_root = sandbox_path(sandbox.workspace)
    git_status, docs = await asyncio.gather(
        sandbox_git_status(sandbox, config, cwd=cwd),
        _root_docs(sandbox, roots, trust_root=trust_root),
    )
    return SandboxWorkspaceContext(
        project_context=render_project_context(str(cwd), git_status), project_docs=docs
    )


async def sandbox_git_status(
    sandbox: SandboxAdapter, config: ProjectContextConfig, *, cwd: Path
) -> str:
    """The git metadata section, as the host renders it for a local repository."""
    timeout = min(config.timeout_seconds, _GIT_TIMEOUT_CAP_SECONDS)
    branch_args, remote_args, log_args = git_metadata_args(config.default_commit_count)
    try:
        branch, remote, log = await asyncio.gather(
            _run_git(sandbox, branch_args, cwd=cwd, timeout=timeout),
            _run_git(sandbox, remote_args, cwd=cwd, timeout=timeout),
            _run_git(sandbox, log_args, cwd=cwd, timeout=timeout),
            return_exceptions=True,
        )
        for result in (branch, log):
            if isinstance(result, BaseException):
                raise result
        remote_branches = None if isinstance(remote, BaseException) else remote
        assert isinstance(branch, str) and isinstance(log, str)
        return format_git_context(branch.strip(), remote_branches, log)
    except TimeoutError:
        return GIT_TIMEOUT_MESSAGE
    except _GitCommandFailedError:
        return GIT_UNAVAILABLE_MESSAGE
    except SANDBOX_FAILURES as exc:
        raise SandboxUnavailableError(f"Sandbox failed to run git: {exc}") from exc


async def find_sandbox_subdirectory_agents_md(
    sandbox: SandboxAdapter,
    file_path: Path,
    roots: Sequence[Path],
    *,
    checked: set[Path] | None = None,
) -> list[tuple[Path, str]]:
    """AGENTS.md docs between a read file's directory and its open root.

    The root itself is excluded: its doc is already in the system
    instructions. Ordered outermost first, like the host's lookup.

    ``checked`` holds the directories already looked up: they are not read
    again. The directories read here are added before the reads start, so a
    write that removes one while they are in flight is not undone, and taken
    out again if a read fails.
    """
    path = sandbox_path(file_path)
    skip = checked if checked is not None else set[Path]()
    for root in roots:
        root = sandbox_path(root)
        if path == root or not path.is_relative_to(root):
            continue
        directories: list[Path] = []
        current = path.parent
        while current != root and current.is_relative_to(root):
            if current not in skip:
                directories.append(current)
            current = current.parent
        if checked is not None:
            checked.update(directories)
        try:
            docs = await asyncio.gather(
                *(_read_agents_md(sandbox, directory) for directory in directories)
            )
        except BaseException:
            if checked is not None:
                checked.difference_update(directories)
            raise
        return [
            (directory, content)
            for directory, content in reversed(
                list(zip(directories, docs, strict=True))
            )
            if content
        ]
    return []


async def find_ignored_project_sources(
    sandbox: SandboxAdapter, *, cwd: Path
) -> list[str]:
    """The project sources in the sandbox's ``cwd`` that a session ignores.

    Of the project's own files, only AGENTS.md docs and skills are read from
    a sandbox: loading its project config, hooks, tools or plugins would run
    code the sandbox controls on the host.
    A sandbox that cannot answer has none to report; the session reports the
    failure itself.
    """
    command = (
        f"for source in {shlex.quote(_PROJECT_DIR)}/*; do "
        'if [ -e "$source" ]; then echo "$source"; fi; done'
    )
    try:
        result = await sandbox.execute(
            command, str(cwd), _PROJECT_SOURCES_TIMEOUT_SECONDS
        )
    except SANDBOX_FAILURES:
        return []
    return sorted(
        line
        for line in result.stdout.splitlines()
        if line.startswith(f"{_PROJECT_DIR}/") and line not in _READ_PROJECT_SOURCES
    )


async def warn_of_ignored_project_sources(sandbox: SandboxAdapter) -> None:
    """Log the project sources a sandboxed run ignores in the sandbox's workspace."""
    ignored = await find_ignored_project_sources(
        sandbox, cwd=sandbox_path(sandbox.workspace)
    )
    if ignored:
        logger.warning(
            "The sandbox working directory has %s, which a sandboxed run "
            "ignores: of the project's own files, only AGENTS.md files and skills "
            "are read from the sandbox.",
            ", ".join(ignored),
        )


async def _root_docs(
    sandbox: SandboxAdapter, roots: Sequence[Path], *, trust_root: Path
) -> list[tuple[Path, str]]:
    directories: dict[Path, None] = {}
    for index, root in enumerate(sandbox_path(root) for root in roots):
        stop = trust_root if index == 0 and root.is_relative_to(trust_root) else root
        # Outermost first, like the host's walk up to its trust root.
        directories.update(dict.fromkeys(reversed(_walk_up(root, stop))))
    unique = list(directories)
    docs = await asyncio.gather(
        *(_read_agents_md(sandbox, directory) for directory in unique)
    )
    return [
        (directory, content)
        for directory, content in zip(unique, docs, strict=True)
        if content
    ]


def _walk_up(start: Path, stop: Path) -> list[Path]:
    """``start`` and its parents up to ``stop``, inclusive, innermost first."""
    directories = [start]
    while directories[-1] != stop:
        directories.append(directories[-1].parent)
    return directories


async def _read_agents_md(sandbox: SandboxAdapter, directory: Path) -> str:
    path = str(directory / AGENTS_MD_FILENAME)
    try:
        raw = await sandbox.read_file(path, AGENTS_MD_MAX_BYTES)
    except SANDBOX_FAILURES as exc:
        raise SandboxUnavailableError(f"Sandbox failed to read {path}: {exc}") from exc
    if raw is None:
        return ""
    return decode_safe(raw).text.strip()


async def _run_git(
    sandbox: SandboxAdapter, args: list[str], *, cwd: Path, timeout: float
) -> str:
    # The same overrides the host applies: no fsmonitor hook from the
    # repository's own config, and no optional locks.
    command = shlex.join(["git", "-c", "core.fsmonitor=", "--no-optional-locks", *args])
    result = await sandbox.execute(command, str(cwd), timeout)
    if result.exit_code != 0:
        raise _GitCommandFailedError(result.stderr)
    return result.stdout


__all__ = [
    "AGENTS_MD_MAX_BYTES",
    "SandboxWorkspaceContext",
    "find_ignored_project_sources",
    "find_sandbox_subdirectory_agents_md",
    "read_sandbox_workspace_context",
    "sandbox_git_status",
    "warn_of_ignored_project_sources",
]
