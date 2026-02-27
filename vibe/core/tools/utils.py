from __future__ import annotations

import fnmatch
from pathlib import Path

from vibe.core.tools.base import ToolPermission


def resolve_path_permission(
    path_str: str, *, allowlist: list[str], denylist: list[str]
) -> ToolPermission | None:
    """Resolve permission for a file path against glob patterns.

    Returns NEVER on denylist match, ALWAYS on allowlist match, None otherwise.
    """
    file_path = Path(path_str).expanduser()
    if not file_path.is_absolute():
        file_path = Path.cwd() / file_path
    file_str = str(file_path.resolve())

    for pattern in denylist:
        if fnmatch.fnmatch(file_str, pattern):
            return ToolPermission.NEVER

    for pattern in allowlist:
        if fnmatch.fnmatch(file_str, pattern):
            return ToolPermission.ALWAYS

    return None


def is_path_within_workdir(path_str: str) -> bool:
    """Return True if the resolved path is inside cwd."""
    file_path = Path(path_str).expanduser()
    if not file_path.is_absolute():
        file_path = Path.cwd() / file_path
    try:
        file_path.resolve().relative_to(Path.cwd().resolve())
        return True
    except ValueError:
        return False


def resolve_file_tool_permission(
    path_str: str,
    *,
    allowlist: list[str],
    denylist: list[str],
    config_permission: ToolPermission,
) -> ToolPermission | None:
    """Resolve permission for a file-based tool invocation.

    Checks allowlist/denylist first, then escalates to ASK for paths outside
    the working directory (unless the tool is configured as NEVER).
    Returns None to fall back to the tool's default config permission.
    """
    if (
        result := resolve_path_permission(
            path_str, allowlist=allowlist, denylist=denylist
        )
    ) is not None:
        return result

    if not is_path_within_workdir(path_str):
        if config_permission == ToolPermission.NEVER:
            return ToolPermission.NEVER
        return ToolPermission.ASK

    return None
