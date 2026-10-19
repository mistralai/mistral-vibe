"""Ports shared by import harness adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ImportDiscoverySummary:
    """Fast project and session counts for a source harness."""

    project_count: int
    session_count: int


@dataclass(frozen=True, slots=True)
class ImportProject:
    """One source project that needs a visible Vibe project destination."""

    name: str
    cwd: Path
    source_cwd: Path | None = None


class ImportProjectProvisioner(Protocol):
    """Makes source project directories visible as Vibe projects."""

    async def ensure_projects(
        self, projects: list[ImportProject]
    ) -> list[ImportProject]: ...


class ImportHarnessPort(Protocol):
    """Provides discovery and import behavior for one external harness."""

    def discover(self) -> ImportDiscoverySummary: ...

    async def run(self) -> None: ...
