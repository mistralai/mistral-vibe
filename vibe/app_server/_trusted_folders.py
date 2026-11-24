"""The ``workspace/trust/folders/*`` RPCs: read, edit and add trust-store entries.

Every call works on the trust file as it is now, which other Vibe processes
may have written, through a manager of its own: the managers of running app
servers are never touched, so a change applies the next time Vibe starts.
Their own later writes only merge their change into the file, so they never
undo one made here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from pathlib import Path
from typing import Any, Concatenate, Literal

from vibe.app_server._dispatch import method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._workspace import WorkspaceTrustError
from vibe.app_server.protocol import (
    TrustFolderCandidate,
    TrustFolderProblem,
    TrustFolderState,
    WorkspaceTrustFoldersAddParams,
    WorkspaceTrustFoldersInspectParams,
    WorkspaceTrustFoldersInspectResponse,
    WorkspaceTrustFoldersListParams,
    WorkspaceTrustFoldersResponse,
    WorkspaceTrustFoldersUpdateParams,
)
from vibe.core.paths import TRUSTED_FOLDERS_FILE
from vibe.core.trusted_folders import (
    TrustedFoldersManager,
    TrustFileError,
    is_in_scratch_dir,
)

TRUST_FOLDER_METHODS = frozenset({
    "workspace/trust/folders/add",
    "workspace/trust/folders/inspect",
    "workspace/trust/folders/list",
    "workspace/trust/folders/update",
})

MAX_SUGGESTIONS = 8
_MAX_UNTRUSTED_INSIDE = 5


async def dispatch_trust_folders(
    method: str, raw_params: dict[str, Any], *, cwd: Callable[[str | None], Path]
) -> ProtocolModel:
    """Answer one ``TRUST_FOLDER_METHODS`` request off the event loop."""
    match method:
        case "workspace/trust/folders/list":
            validate_wire(WorkspaceTrustFoldersListParams, raw_params)
            return await asyncio.to_thread(_in_file, read_trusted_folders)
        case "workspace/trust/folders/update":
            update = validate_wire(WorkspaceTrustFoldersUpdateParams, raw_params)
            return await asyncio.to_thread(
                _in_file, update_trusted_folder, update.path, update.state
            )
        case "workspace/trust/folders/inspect":
            inspect = validate_wire(WorkspaceTrustFoldersInspectParams, raw_params)
            return await asyncio.to_thread(
                _in_file, inspect_trust_folder, inspect.path, cwd(inspect.cwd)
            )
        case "workspace/trust/folders/add":
            add = validate_wire(WorkspaceTrustFoldersAddParams, raw_params)
            return await asyncio.to_thread(
                _in_file, add_trusted_folder, add.path, cwd(add.cwd)
            )
        case _:
            raise method_not_found(method)


def _in_file[**P, R](
    handler: Callable[Concatenate[TrustedFoldersManager, P], R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Run *handler* on the trust file as it is now.

    A file that cannot be read or written is an error the client shows, never
    a success the store did not record.
    """
    try:
        return handler(TrustedFoldersManager.read_file(), *args, **kwargs)
    except (OSError, TrustFileError) as exc:
        raise WorkspaceTrustError(
            f"Cannot update {TRUSTED_FOLDERS_FILE.path}: {exc}"
        ) from exc


def read_trusted_folders(
    trust_store: TrustedFoldersManager,
) -> WorkspaceTrustFoldersResponse:
    decisions = trust_store.persisted_decisions()
    return WorkspaceTrustFoldersResponse(
        trusted=decisions["trusted"],
        untrusted=decisions["untrusted"],
        settings_path=str(TRUSTED_FOLDERS_FILE.path),
    )


def update_trusted_folder(
    trust_store: TrustedFoldersManager, path: str, state: TrustFolderState
) -> WorkspaceTrustFoldersResponse:
    """Move or drop one stored entry; home and its ancestors are never trusted."""
    if state == "trusted" and (problem := _problem(_resolve(path, Path.cwd()))):
        raise WorkspaceTrustError(f"Cannot trust {path}: {problem}")
    target = None if state == "undecided" else state == "trusted"
    if not trust_store.edit(path, target):
        raise WorkspaceTrustError(f"No trust decision is stored for {path}")
    return read_trusted_folders(trust_store)


def inspect_trust_folder(
    trust_store: TrustedFoldersManager, path: str, cwd: Path
) -> WorkspaceTrustFoldersInspectResponse:
    """The typed folder or its closest existing parent, then the folders completing the path.

    ``/a/b/`` offers ``/a/b/`` and its children; ``/a/b`` offers ``/a/b/`` and
    the folders of ``/a`` starting with ``b``; a missing ``/a/b/c`` offers
    ``/a/b/`` and its children starting with ``c``.
    """
    text = path.strip()
    typed = _resolve(text, cwd)
    typed_problem: Literal["missing", "not_directory"] | None = None
    if not _is_folder(typed, path):
        typed_problem = "missing" if not _exists(typed, path) else "not_directory"
    folder = _closest_folder(typed, path)
    first = _first_value(text if typed_problem is None else "", folder, cwd)
    head, prefix = _split_last_segment(text)
    completions = [
        value
        for value in _children(head, prefix, cwd)
        if _resolve(value, cwd) != folder
    ]
    decisions = trust_store.persisted_decisions()
    candidates = [
        _candidate(value, cwd, trust_store, decisions)
        for value in [first, *completions][: MAX_SUGGESTIONS + 1]
    ]
    return WorkspaceTrustFoldersInspectResponse(
        typed_problem=typed_problem, candidates=candidates
    )


def add_trusted_folder(
    trust_store: TrustedFoldersManager, path: str, cwd: Path
) -> WorkspaceTrustFoldersResponse:
    """Trust *path* and everything under it that no closer entry untrusts."""
    resolved = _resolve(path, cwd)
    if not _is_folder(resolved, path):
        raise WorkspaceTrustError(f"No folder at {path}")
    if problem := _problem(resolved):
        raise WorkspaceTrustError(f"Cannot trust {path}: {problem}")
    trust_store.trust(resolved)
    return read_trusted_folders(trust_store)


def _candidate(
    value: str,
    cwd: Path,
    trust_store: TrustedFoldersManager,
    decisions: dict[str, list[str]],
) -> TrustFolderCandidate:
    resolved = _resolve(value, cwd)
    key = str(resolved)
    stored = (
        "trusted"
        if key in decisions["trusted"]
        else "untrusted"
        if key in decisions["untrusted"]
        else None
    )
    root = trust_store.find_trust_root(resolved)
    inside = sorted(
        entry for entry in decisions["untrusted"] if resolved in Path(entry).parents
    )
    return TrustFolderCandidate(
        path=key,
        value=value,
        problem=_problem(resolved),
        stored=stored,
        trusted_by=str(root) if root is not None and root != resolved else None,
        untrusted_inside=inside[:_MAX_UNTRUSTED_INSIDE],
        untrusted_inside_count=len(inside),
    )


def _resolve(path: str, cwd: Path) -> Path:
    """*path* as typed, absolute and resolved; one no folder can have is an error."""
    try:
        return (cwd / Path(path.strip()).expanduser()).resolve()
    except (RuntimeError, ValueError, OSError) as exc:
        # An unknown `~user`, a NUL byte, a symlink loop.
        raise WorkspaceTrustError(f"Not a usable path: {path}") from exc


def _problem(folder: Path) -> TrustFolderProblem | None:
    # Trust walks down to every descendant: the home directory, a folder
    # holding it, or a filesystem root would trust nearly every project.
    if folder.parent == folder:
        return "root"
    home = Path.home().resolve()
    # Compared as files, not strings: a case-insensitive disk spells home many ways.
    if any(_same_folder(folder, holder) for holder in (home, *home.parents)):
        return "home"
    # No decision trusts a scratch folder, so storing one would mislead.
    if is_in_scratch_dir(folder):
        return "scratch"
    return None


def _same_folder(path: Path, other: Path) -> bool:
    try:
        return path.samefile(other)
    except OSError:
        return path == other


def _closest_folder(path: Path, typed: str) -> Path:
    """*path* when it is a folder, else its closest ancestor that is one."""
    while not _is_folder(path, typed) and path.parent != path:
        path = path.parent
    return path


def _is_folder(path: Path, typed: str) -> bool:
    try:
        return path.is_dir()
    except OSError as exc:
        # A folder the user cannot read, or a name longer than the system allows.
        raise WorkspaceTrustError(f"Not a usable path: {typed}") from exc


def _exists(path: Path, typed: str) -> bool:
    try:
        return path.exists()
    except OSError as exc:
        raise WorkspaceTrustError(f"Not a usable path: {typed}") from exc


def _split_last_segment(text: str) -> tuple[str, str]:
    """``(head ending in a separator, last segment)``; no head without a separator."""
    cut = max(text.rfind(sep) for sep in (os.sep, os.altsep) if sep)
    if cut < 0:
        return "", text
    return text[: cut + 1], text[cut + 1 :]


def _first_value(text: str, folder: Path, cwd: Path) -> str:
    """*text* as typed when, as a folder value, it still names *folder*, else *folder* itself.

    On Windows a bare drive like ``C:`` is the current folder on that drive, but
    ``C:\\`` is its root.
    """
    if text and _resolve(value := _as_folder_value(text), cwd) == folder:
        return value
    return _as_folder_value(str(folder))


def _as_folder_value(text: str) -> str:
    """*text* ending in exactly one separator, so the field lists its children next."""
    if text.endswith((os.sep, os.altsep or os.sep)):
        return text
    return text + os.sep


def _children(head: str, prefix: str, cwd: Path) -> list[str]:
    """Child folders of *head* starting with *prefix*, as field values."""
    parent = _resolve(head, cwd) if head else cwd.resolve()
    try:
        with os.scandir(parent) as entries:
            names = sorted(
                entry.name
                for entry in entries
                if entry.name.startswith(prefix)
                # Hidden folders only once the user asks for them.
                and (prefix.startswith(".") or not entry.name.startswith("."))
                and _entry_is_dir(entry)
            )
    except OSError:
        return []
    return [f"{head}{name}{os.sep}" for name in names[:MAX_SUGGESTIONS]]


def _entry_is_dir(entry: os.DirEntry[str]) -> bool:
    """Whether *entry* is a folder; one that cannot be checked, like a symlink loop, is skipped."""
    try:
        return entry.is_dir()
    except OSError:
        return False
