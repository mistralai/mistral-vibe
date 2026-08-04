from __future__ import annotations

import fnmatch
from pathlib import Path, PurePath

from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.scratchpad import is_scratchpad_path
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)


def _make_absolute(path_str: str, cwd: Path) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _configured_project_roots() -> list[Path]:
    try:
        return get_harness_files_manager().project_roots
    except RuntimeError:
        return []


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

    for pattern in sensitive_patterns:
        if PurePath(file_str).match(pattern):
            required.append(
                RequiredPermission(
                    scope=PermissionScope.FILE_PATTERN,
                    invocation_pattern=file_path.name,
                    session_pattern="*",
                    label=f"accessing sensitive files ({tool_name})",
                )
            )
            break

    if not is_path_within_workdir(path_str, cwd=cwd, project_roots=project_roots):
        if config_permission == ToolPermission.NEVER:
            return PermissionContext(permission=ToolPermission.NEVER)
        resolved = file_path.resolve()
        parent_dir = str(resolved.parent)
        glob = str(Path(parent_dir) / "*")
        required.append(
            RequiredPermission(
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                invocation_pattern=glob,
                session_pattern=glob,
                label=f"outside workdir ({glob})",
            )
        )

    if required:
        return PermissionContext(
            permission=ToolPermission.ASK, required_permissions=required
        )

    return None
