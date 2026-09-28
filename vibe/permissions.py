from __future__ import annotations

from enum import StrEnum, auto
import fnmatch
import ntpath
from pathlib import PurePosixPath, PureWindowsPath
import posixpath

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

__all__ = [
    "PathGrantScope",
    "PermissionScope",
    "RequiredPermission",
    "path_grant_pattern",
    "path_grant_pattern_matches",
    "path_pattern_matches",
    "scope_required_permissions",
]


_PATH_GRANT_PREFIX = "vibe-path"


class PathGrantScope(StrEnum):
    EXACT = auto()
    DIRECTORY_RECURSIVE = auto()


class PermissionScope(StrEnum):
    COMMAND_PATTERN = auto()
    OUTSIDE_DIRECTORY = auto()
    FILE_PATTERN = auto()
    URL_PATTERN = auto()


class RequiredPermission(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )

    scope: PermissionScope
    invocation_pattern: str
    session_pattern: str
    label: str
    literal: bool = Field(default=False, exclude=True)
    """Match this scope as text, never as a glob.

    A scope recorded because nothing wider was honest has to stay out of reach of
    the wider rules: ``cat *`` is a fair grant for ``cat $FILE`` and would
    otherwise cover ``cat notes > /etc/cron.d/pwn``, whose whole point is that
    only the command as written may be granted. The command's own text can carry
    glob characters too, so ``git log *`` must not read as a pattern either.

    Excluded from serialization: only ``covers`` reads it, and every caller
    resolves the permission afresh from the call. Putting it on the wire would
    add a field to the ACP and Runtime payloads that nothing downstream reads.
    """
    path_scope_root: str | None = None


def _is_windows_path(path: str) -> bool:
    return bool(PureWindowsPath(path).drive) or "\\" in path


def _normalized_path(path: str) -> str:
    if _is_windows_path(path):
        return ntpath.normcase(ntpath.normpath(path))
    return posixpath.normpath(path)


def path_grant_pattern(path: str, scope: PathGrantScope) -> str:
    """Encode a literal path grant without giving glob metacharacters meaning."""
    return f"{_PATH_GRANT_PREFIX}:{scope.value}:{_normalized_path(path)}"


def _parse_path_grant_pattern(pattern: str) -> tuple[PathGrantScope, str] | None:
    prefix, separator, remainder = pattern.partition(":")
    if prefix != _PATH_GRANT_PREFIX or not separator:
        return None
    raw_scope, separator, path = remainder.partition(":")
    if not separator:
        return None
    try:
        return PathGrantScope(raw_scope), _normalized_path(path)
    except ValueError:
        return None


def path_pattern_matches(path: str, pattern: str) -> bool:
    """Match encoded grants and legacy path globs without crossing separators."""
    normalized = _normalized_path(path)
    if parsed := _parse_path_grant_pattern(pattern):
        scope, granted = parsed
        if scope is PathGrantScope.EXACT:
            return normalized == granted
        separator = "\\" if _is_windows_path(granted) else "/"
        return normalized == granted or normalized.startswith(
            granted.rstrip(separator) + separator
        )

    windows = _is_windows_path(path) or _is_windows_path(pattern)
    path_cls = PureWindowsPath if windows else PurePosixPath
    if path_cls(pattern).is_absolute():
        return path_cls(normalized).match(pattern)
    # Path.match right-anchors relative patterns, so ``tmp/*`` would match
    # ``/var/tmp/secret``. fnmatch requires the whole path to match.
    if windows:
        pattern = ntpath.normcase(pattern)
    return fnmatch.fnmatch(normalized, pattern)


def _is_legacy_path_grant_pattern(pattern: str) -> bool:
    """True for absolute path globs such as ``/tmp/*`` or ``C:\\tmp\\*``.

    Shell allowlists also hold command wildcards (``*``, ``npm *``). Those must
    not clear outside-workdir checks just because ``fnmatch`` would accept them
    as path patterns.
    """
    if not any(character in pattern for character in "*?["):
        return False
    windows = _is_windows_path(pattern)
    path_cls = PureWindowsPath if windows else PurePosixPath
    return path_cls(pattern).is_absolute()


def path_grant_pattern_matches(path: str, pattern: str) -> bool:
    """Match an encoded path grant or a legacy wildcard path grant.

    Shell allowlists also contain literal command and executable entries. Those
    must not become path grants merely because their text equals a path.
    """
    return (
        _parse_path_grant_pattern(pattern) is not None
        or _is_legacy_path_grant_pattern(pattern)
    ) and path_pattern_matches(path, pattern)


def scope_required_permissions(
    required_permissions: list[RequiredPermission], scope: PathGrantScope
) -> list[RequiredPermission]:
    """Apply one server-validated path scope to outside-workdir permissions."""
    scoped: list[RequiredPermission] = []
    for permission in required_permissions:
        if permission.scope is not PermissionScope.OUTSIDE_DIRECTORY:
            scoped.append(permission)
            continue
        target = _normalized_path(permission.invocation_pattern)
        grant_root = (
            target
            if scope is PathGrantScope.EXACT
            else _normalized_path(permission.path_scope_root or target)
        )
        scoped.append(
            permission.model_copy(
                update={"session_pattern": path_grant_pattern(grant_root, scope)}
            )
        )
    return scoped
