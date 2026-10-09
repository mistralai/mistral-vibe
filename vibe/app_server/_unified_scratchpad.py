"""A Unified Harness session's scratchpad: private per-session files for the agent.

Design: ``vibe/docs/design/unified-harness-todo-and-scratchpad.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

_SCRATCHPAD_DIRNAME = "scratchpad"


def scratchpad_dir(storage_root: str | Path, session_id: str) -> Path:
    # Mirrors the Host's private `_session_root`, so the notes share the session's
    # lifetime.
    return (
        Path(storage_root).expanduser().resolve()
        / "unified"
        / session_id
        / _SCRATCHPAD_DIRNAME
    )


SCRATCHPAD_TOOL_NAME = "unified_harness_scratchpad"

SCRATCHPAD_TOOL_DESCRIPTION = """\
Read and write files in your scratchpad: a private, per-session directory for \
working notes, findings, and fetched data.

Use it for anything you will need later in a long task. Files kept here outlive \
the messages that produced them: after the conversation is summarised, list and \
read them back to recover what you saved.

Paths are relative to the scratchpad directory; nested paths such as \
'research/api-notes.md' are fine. A write replaces the file's whole contents. Do \
not use this for files that belong to the user's project -- use the filesystem \
tools for those."""


class ScratchpadArgs(BaseModel):
    action: Literal["read", "write", "list"] = Field(
        description=(
            "Required on every call: 'write' to save a file, 'read' to fetch one "
            "back, or 'list' to see what the scratchpad holds"
        )
    )
    path: str | None = Field(
        default=None,
        description=(
            "Required for 'read' and 'write': a path relative to the scratchpad "
            "directory, for example 'findings.md'"
        ),
    )
    content: str | None = Field(
        default=None, description="Required for 'write': the file's full new contents"
    )


class ScratchpadResult(BaseModel):
    verb: str
    path: str | None = None
    content: str | None = None
    files: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def message(self) -> str:
        if self.path is not None:
            return f"{self.verb} {self.path}"
        return f"{self.verb} {len(self.files)} files"


class ScratchpadError(ValueError):
    pass


def run_scratchpad(args: ScratchpadArgs, *, directory: Path) -> ScratchpadResult:
    if args.action == "list":
        return ScratchpadResult(verb="Listed", files=_list_files(directory))

    if not args.path:
        raise ScratchpadError(f"'path' is required when action='{args.action}'")

    target = _resolve_within(directory, args.path)
    _reject_hard_link(target, args.path)

    if args.action == "read":
        if not target.is_file():
            raise ScratchpadError(f"No such file in the scratchpad: {args.path}")
        return ScratchpadResult(
            verb="Read",
            path=args.path,
            content=target.read_text(encoding="utf-8", errors="replace"),
        )

    if args.content is None:
        raise ScratchpadError("'content' is required when action='write'")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content, encoding="utf-8")
    return ScratchpadResult(verb="Wrote", path=args.path)


def _list_files(directory: Path) -> list[str]:
    """Every regular file the scratchpad genuinely contains, as relative names.

    Enumeration repeats the `_resolve_within` confinement the write path enforces:
    the filesystem tools can plant a symlink here, and `is_file()` would otherwise
    list a file that lives outside the scratchpad.
    """
    if not directory.is_dir():
        return []
    root = directory.resolve()
    found: list[str] = []
    for path in root.rglob("*"):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_file() or root not in resolved.parents:
            continue
        if _hard_linked(resolved):
            continue
        found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _hard_linked(path: Path) -> bool:
    """Whether something outside the scratchpad may name this file's inode too.

    A hard link has no target to resolve, so the confinement checks above cannot
    tell that it reaches outside: reading one leaks the aliased file, and writing
    one truncates it. Windows reports 0 or 1 links, so only a count above 1
    rejects.
    """
    try:
        return path.stat().st_nlink > 1
    except OSError:
        return False


def _reject_hard_link(target: Path, path: str) -> None:
    if _hard_linked(target):
        raise ScratchpadError(
            f"Path is a hard link and may alias a file outside the scratchpad: {path}"
        )


def _resolve_within(directory: Path, path: str) -> Path:
    # Resolve before checking, so `..`, absolute paths and symlinks are all caught
    # by the same test rather than by pattern-matching the string.
    resolved = (directory / path).resolve()
    if resolved != directory and directory not in resolved.parents:
        raise ScratchpadError(f"Path escapes the scratchpad directory: {path}")
    if resolved == directory:
        raise ScratchpadError("Path must name a file inside the scratchpad")
    return resolved


__all__ = [
    "SCRATCHPAD_TOOL_DESCRIPTION",
    "SCRATCHPAD_TOOL_NAME",
    "ScratchpadArgs",
    "ScratchpadError",
    "ScratchpadResult",
    "run_scratchpad",
    "scratchpad_dir",
]
