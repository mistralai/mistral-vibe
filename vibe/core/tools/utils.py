from __future__ import annotations

from collections.abc import Iterable, Mapping
import fnmatch
import glob
import os
from pathlib import Path, PurePath
import re
import sys
from typing import Annotated

from pydantic import AfterValidator

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.workspace import Workspace
from vibe.permissions import (
    PathGrantScope,
    parse_path_grant_pattern,
    path_grant_pattern,
    path_grant_pattern_matches,
    path_pattern_matches,
)
from vibe.utils.paths import (
    normalize_windows_input_path,
    normalize_windows_path,
    target_pure_path,
)

# A model-supplied path argument, normalized once when the tool call is validated.
ToolPath = Annotated[str, AfterValidator(normalize_windows_input_path)]

DEFAULT_SENSITIVE_PATTERNS: list[str] = [
    "**/.env",
    "**/.env.*",
    "**/.env~",
    "**/.envrc",
    "**/.envrc.*",
    "**/.envrc~",
]


def matches_sensitive_pattern(resolved_path: str, patterns: list[str]) -> bool:
    """Return True if a resolved path matches any sensitive glob, case-insensitively."""
    lowered = PurePath(resolved_path.lower())
    return any(lowered.match(pattern.lower()) for pattern in patterns)


def _make_absolute(path_str: str, cwd: Path) -> Path:
    path = Path(normalize_windows_path(path_str)).expanduser()
    if target_pure_path(str(path)).is_absolute():
        return path
    return cwd / path


def resolve_tool_path(raw: str | None, cwd: Path) -> Path:
    """Resolve a model-supplied path against the tool's working directory."""
    if not raw:
        return cwd
    path = _make_absolute(raw, cwd)
    # resolve() would anchor a Windows path held by a POSIX host to the process cwd.
    return path.resolve() if path.is_absolute() else path


def shell_path_scope_root(path_str: str) -> str | None:
    """Return a recursive grant root only for a known, traversable directory."""
    target = Path(path_str)
    try:
        if target.is_dir() and os.access(target, os.R_OK | os.X_OK):
            return str(target)
    except OSError:
        pass
    return None


def ambient_workspace() -> Workspace:
    """Fallback workspace for a caller with no session, rooted at the process cwd."""
    try:
        project_roots = get_harness_files_manager().project_roots
    except RuntimeError:
        project_roots = []
    return Workspace.for_session(Path.cwd(), project_roots)


def resolve_path_permission(
    path_str: str, *, workspace: Workspace, allowlist: list[str], denylist: list[str]
) -> PermissionContext | None:
    """Resolve permission for a file path against glob patterns.

    Returns NEVER on denylist match, ALWAYS on allowlist match, None otherwise.
    """
    file_str = str(_make_absolute(path_str, workspace.cwd).resolve())

    for pattern in _with_resolved_path_globs(denylist):
        if fnmatch.fnmatch(file_str, pattern):
            return PermissionContext(permission=ToolPermission.NEVER)

    for pattern in with_resolved_path_grants(allowlist, workspace):
        if path_pattern_matches(file_str, pattern):
            return PermissionContext(permission=ToolPermission.ALWAYS)

    return None


def _with_resolved_path_globs(patterns: Iterable[str]) -> list[str]:
    """The globs, plus each absolute glob with the links in its literal prefix followed.

    Deny entries must cover every spelling allowlist grants match, or ``/tmp/secret``
    would not deny ``/private/tmp/secret`` that a ``/tmp`` grant allows on macOS.
    The original glob is kept, so following links only ever widens a denial.
    """
    expanded: list[str] = []
    for pattern in patterns:
        expanded.append(pattern)
        resolved = _resolved_path_glob(pattern)
        if resolved is not None and resolved != pattern:
            expanded.append(resolved)
    return expanded


def _resolved_path_glob(pattern: str) -> str | None:
    path = Path(pattern)
    if not path.is_absolute():
        return None
    parts = path.parts
    literal = next(
        (i for i, part in enumerate(parts) if any(c in part for c in "*?[")), len(parts)
    )
    try:
        prefix = Path(*parts[:literal]).resolve()
    except (OSError, ValueError):
        return None
    return str(prefix.joinpath(*parts[literal:]))


def with_resolved_path_grants(
    patterns: Iterable[str], workspace: Workspace
) -> list[str]:
    """The patterns, plus each absolute path grant or glob with its links followed.

    Tools match the resolved path a call reaches (``/tmp`` is ``/private/tmp`` on
    macOS), but a grant written by hand keeps the spelling the user typed. Links
    the agent could repoint are not followed, or repointing one would move the
    grant to a directory the user never named: a link inside the workspace, and
    the last literal component unless it is one of macOS's system links.
    """
    expanded: list[str] = []
    for pattern in patterns:
        expanded.append(pattern)
        resolved = (
            _resolved_path_grant(pattern, workspace)
            if parse_path_grant_pattern(pattern) is not None
            else _resolved_allow_glob(pattern, workspace)
        )
        if resolved is not None and resolved != pattern:
            expanded.append(resolved)
    return expanded


def _resolved_path_grant(pattern: str, workspace: Workspace) -> str | None:
    if (parsed := parse_path_grant_pattern(pattern)) is None:
        return None
    scope, granted = parsed
    path = Path(granted)
    if not path.is_absolute():
        return None
    if (target := _resolved_written_path(path, workspace)) is None:
        return None
    return path_grant_pattern(str(target), scope)


def _resolved_allow_glob(pattern: str, workspace: Workspace) -> str | None:
    path = Path(pattern)
    if not path.is_absolute():
        return None
    parts = path.parts
    literal = next(
        (i for i, part in enumerate(parts) if any(c in part for c in "*?[")), len(parts)
    )
    # A wildcard right below the root leaves no literal component to follow.
    if literal <= 1:
        return None
    prefix = _resolved_written_path(Path(*parts[:literal]), workspace)
    if prefix is None:
        return None
    return str(prefix.joinpath(*parts[literal:]))


def _resolved_written_path(path: Path, workspace: Workspace) -> Path | None:
    """``path`` with its parents' links followed, outside the workspace only."""
    if (parent := _follow_links_outside(path.parent, workspace)) is None:
        return None
    target = str(parent / path.name)
    if sys.platform == "darwin":
        target = _MACOS_SYSTEM_LINKS.get(target, target)
    return Path(target)


def _follow_links_outside(path: Path, workspace: Workspace) -> Path | None:
    """``path`` with its links followed, or ``None`` if one lies in the workspace."""
    followed = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            # ``..`` climbs from where the links led, as the OS reads it.
            followed = followed.parent if part == ".." else followed / part
            if not (followed.is_symlink() or followed.is_junction()):
                continue
            if workspace.allows(followed):
                return None
            followed = followed.resolve()
    except (OSError, ValueError):
        return None
    return followed


_MACOS_SYSTEM_LINKS = {
    "/etc": "/private/etc",
    "/tmp": "/private/tmp",
    "/var": "/private/var",
}


def ungranted_paths(
    paths: Mapping[str, str], allowlist: Iterable[str], workspace: Workspace
) -> dict[str, str]:
    """The resolved paths no path grant of ``allowlist`` covers, symlinks followed.

    ``paths`` maps each resolved path to the spelling the call supplied for it.
    """
    grants = with_resolved_path_grants(allowlist, workspace)
    return {
        path: supplied
        for path, supplied in paths.items()
        if not any(path_grant_pattern_matches(path, grant) for grant in grants)
    }


def names_a_link(token: str, cwd: Path) -> bool:
    """Whether a bare name is a symlink or a Windows junction in ``cwd``.

    Either can lead anywhere a path can, so the name has to be resolved like one.
    """
    try:
        candidate = cwd / token
        return candidate.is_symlink() or candidate.is_junction()
    except (OSError, ValueError):
        return False


_STREAM_DEVICES = frozenset({"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"})
_FD_DEVICE = re.compile(r"/dev/fd/[0-9]+")


def is_stream_device_path(path_str: str) -> bool:
    """Whether the path names the null device or a standard stream, not a file.

    Read as written: ``..`` after ``/dev/fd/3`` climbs from wherever that
    descriptor points, so normalizing it away would hide the real target.
    """
    path = path_str.rstrip("/")
    return path in _STREAM_DEVICES or _FD_DEVICE.fullmatch(path) is not None


def is_path_within_workdir(
    path_str: str, *, workspace: Workspace | None = None
) -> bool:
    """Return True if the resolved path is inside the workspace's authorised roots.

    Omitting ``workspace`` resolves against the process cwd, not any session's.
    """
    workspace = workspace or ambient_workspace()
    try:
        resolved = _make_absolute(path_str, workspace.cwd).resolve()
    except (ValueError, OSError):
        return False
    return workspace.allows(resolved)


def resolve_file_tool_permission(
    path_str: str,
    *,
    tool_name: str,
    allowlist: list[str],
    denylist: list[str],
    config_permission: ToolPermission,
    sensitive_patterns: list[str],
    workspace: Workspace | None = None,
    scratchpad_dir: Path | None = None,
) -> PermissionContext | None:
    """Resolve permission for a file-based tool invocation.

    Checks scratchpad, then allowlist/denylist, then sensitive patterns, then workdir boundary.
    Returns PermissionContext with granular required_permissions when applicable.
    """
    workspace = workspace or ambient_workspace()
    cwd = workspace.cwd

    if is_scratchpad_path(path_str, scratchpad_dir=scratchpad_dir):
        return PermissionContext(permission=ToolPermission.ALWAYS)

    if (
        result := resolve_path_permission(
            path_str, workspace=workspace, allowlist=allowlist, denylist=denylist
        )
    ) is not None:
        return result

    required: list[RequiredPermission] = []

    file_path = _make_absolute(path_str, cwd)
    file_str = str(file_path.resolve())

    if matches_sensitive_pattern(file_str, sensitive_patterns):
        # Scope the grant to this file's name so approving one sensitive file
        # for the session (or permanently) does not silently cover every
        # other sensitive file. A different sensitive file re-prompts.
        required.append(
            RequiredPermission(
                scope=PermissionScope.FILE_PATTERN,
                invocation_pattern=file_str,
                session_pattern=glob.escape(file_str),
                label=f"accessing sensitive files ({tool_name})",
            )
        )

    if not is_path_within_workdir(path_str, workspace=workspace):
        if config_permission == ToolPermission.NEVER:
            return PermissionContext(permission=ToolPermission.NEVER)
        resolved = str(file_path.resolve())
        required.append(
            RequiredPermission(
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                invocation_pattern=resolved,
                session_pattern=path_grant_pattern(resolved, PathGrantScope.EXACT),
                label=f"outside workdir ({resolved})",
                path_scope_root=shell_path_scope_root(resolved),
                supplied_pattern=str(file_path),
            )
        )

    if required:
        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=required
        )

    return None
