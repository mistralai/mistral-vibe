from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from vibe.core.paths import dedup_paths


@dataclass(frozen=True)
class Workspace:
    """Where a session sits, and what it is allowed to write to.

    ``cwd`` resolves relative paths. ``authorized_roots`` is the whole of the
    write boundary, and the working directory is a member of it rather than an
    implicit extra, so the boundary can be replaced as a whole instead of being
    recomputed from wherever the session happens to sit.
    """

    cwd: Path
    authorized_roots: tuple[Path, ...]

    @classmethod
    def for_session(cls, cwd: Path, project_roots: Iterable[Path] = ()) -> Workspace:
        """Build the workspace a session starts with.

        The working directory is authorised whether or not it is trusted or
        listed as a project root, which is what keeps an unconfigured or
        untrusted directory usable.
        """
        resolved = cwd.resolve()
        return cls(
            cwd=resolved,
            authorized_roots=tuple(dedup_paths([resolved, *project_roots])),
        )

    def allows(self, resolved_path: Path) -> bool:
        """Whether an already-resolved path lies inside the write boundary."""
        return any(resolved_path.is_relative_to(root) for root in self.authorized_roots)
