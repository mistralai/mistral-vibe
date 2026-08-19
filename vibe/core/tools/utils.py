from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import fnmatch
import glob
import os
from pathlib import Path, PurePath
from typing import Annotated

from pydantic import AfterValidator

from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
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


_active_file_display_harness: ContextVar[HarnessFilesManager | None] = ContextVar(
    "active_file_display_harness", default=None
)


@contextmanager
def file_display_harness(harness_files: HarnessFilesManager | None) -> Iterator[None]:
    if harness_files is None:
        yield
        return

    token = _active_file_display_harness.set(harness_files)
    try:
        yield
    finally:
        _active_file_display_harness.reset(token)


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


def _configured_project_roots() -> list[Path]:
    try:
        return get_harness_files_manager().project_roots
    except RuntimeError:
        return []


def _resolve_display_target(path_str: str, cwd: Path) -> Path | None:
    """Resolve user-provided absolute, relative, or home-relative paths."""
    try:
        path = Path(path_str).expanduser()
        if not path.is_absolute():
            path = cwd / path
        return Path(os.path.normpath(path))
    except (ValueError, OSError):
        return None


def _display_relative_to_cwd(path: Path, cwd: Path) -> str | None:
    """Return a stable cwd-relative display path, or None when outside cwd."""
    try:
        rel = path.relative_to(cwd)
    except ValueError:
        return None
    if str(rel) == ".":
        return path.name
    return str(rel)


def display_file_path(path_str: str) -> str:
    """Path relative to the session cwd, for display.

    Falls back to the original string when the path can't be resolved, or the
    resolved absolute path when it does not sit under the session cwd.
    """
    manager = _active_file_display_harness.get()
    cwd_raw = (
        (manager.cwd or Path.cwd()).expanduser() if manager is not None else Path.cwd()
    )
    cwd = Path(os.path.normpath(str(cwd_raw)))
    path = _resolve_display_target(path_str, cwd)
    if path is None:
        return path_str

    if relative_path := _display_relative_to_cwd(path, cwd):
        return relative_path
    return str(path)


def resolve_path_permission(
    path_str: str, *, cwd: Path, allowlist: list[str], denylist: list[str]
) -> PermissionContext | None:
    """Resolve permission for a file path against glob patterns.

    Returns NEVER on denylist match, ALWAYS on allowlist match, None otherwise.
    """
    file_str = str(_make_absolute(path_str, cwd).resolve())

    for pattern in denylist:
        if fnmatch.fnmatch(file_str, pattern):
            return PermissionContext(permission=ToolPermission.NEVER)

    for pattern in allowlist:
        if fnmatch.fnmatch(file_str, pattern):
            return PermissionContext(permission=ToolPermission.ALWAYS)

    return None


def is_path_within_workdir(
    path_str: str, *, cwd: Path | None = None, project_roots: list[Path] | None = None
) -> bool:
    """Return True if the resolved path is inside cwd or any project root.

    Project roots come from the harness manager (trusted cwd + ``--add-dir``).
    cwd is always in-bounds, even when the manager isn't initialized or when
    the project source is disabled.
    """
    cwd = (cwd or Path.cwd()).resolve()
    if project_roots is None:
        project_roots = _configured_project_roots()
    try:
        resolved = _make_absolute(path_str, cwd).resolve()
    except (ValueError, OSError):
        return False
    if resolved.is_relative_to(cwd.resolve()):
        return True
    return any(resolved.is_relative_to(root.resolve()) for root in project_roots)


def resolve_file_tool_permission(
    path_str: str,
    *,
    tool_name: str,
    allowlist: list[str],
    denylist: list[str],
    config_permission: ToolPermission,
    sensitive_patterns: list[str],
    cwd: Path | None = None,
    project_roots: list[Path] | None = None,
    scratchpad_dir: Path | None = None,
) -> PermissionContext | None:
    """Resolve permission for a file-based tool invocation.

    Checks scratchpad, then allowlist/denylist, then sensitive patterns, then workdir boundary.
    Returns PermissionContext with granular required_permissions when applicable.
    """
    cwd = (cwd or Path.cwd()).resolve()
    if project_roots is None:
        project_roots = _configured_project_roots()

    if is_scratchpad_path(path_str, scratchpad_dir=scratchpad_dir):
        return PermissionContext(permission=ToolPermission.ALWAYS)

    if (
        result := resolve_path_permission(
            path_str, cwd=cwd, allowlist=allowlist, denylist=denylist
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

    if not is_path_within_workdir(path_str, cwd=cwd, project_roots=project_roots):
        if config_permission == ToolPermission.NEVER:
            return PermissionContext(permission=ToolPermission.NEVER)
        resolved = file_path.resolve()
        parent_dir = str(resolved.parent)
        parent_glob = str(Path(parent_dir) / "*")
        required.append(
            RequiredPermission(
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                invocation_pattern=parent_glob,
                session_pattern=parent_glob,
                label=f"outside workdir ({parent_glob})",
            )
        )

    if required:
        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=required
        )

    return None
