from __future__ import annotations

from pathlib import Path

from vibe.core.paths import AGENTS_MD_FILENAME, VIBE_HOME
from vibe.core.utils.io import read_safe

_MEMORIES_HEADING = "## Memories"


def user_agents_md_path() -> Path:
    return VIBE_HOME.path / AGENTS_MD_FILENAME


def append_user_memory(note: str, *, path: Path | None = None) -> Path:
    """Append a bullet note under a '## Memories' section in the user AGENTS.md.

    Creates the file and the section if they don't exist yet.
    """
    note = note.strip()
    if not note:
        raise ValueError("Memory note cannot be empty.")

    target = path or user_agents_md_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = read_safe(target).text if target.exists() else ""
    target.write_text(_with_memory(existing, note), encoding="utf-8")
    return target


def _with_memory(existing: str, note: str) -> str:
    bullet = f"- {note}"
    if _MEMORIES_HEADING not in existing:
        prefix = existing.rstrip("\n")
        separator = "\n\n" if prefix else ""
        return f"{prefix}{separator}{_MEMORIES_HEADING}\n\n{bullet}\n"

    lines = existing.splitlines()
    insert_at = _last_memory_line(lines)
    lines.insert(insert_at, bullet)
    return "\n".join(lines) + "\n"


def _last_memory_line(lines: list[str]) -> int:
    heading_idx = next(
        i for i, line in enumerate(lines) if line.strip() == _MEMORIES_HEADING
    )
    insert_at = len(lines)
    for i in range(heading_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            insert_at = i
            break
    while insert_at > heading_idx + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    return insert_at
