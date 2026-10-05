"""How far a worktree's creation has come, for a caller that shows it.

Creating a worktree in a large repository takes seconds, most of them spent
writing its files. These values are what a front end needs to say which part
it is in and, while files are being written, how many are done.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class WorktreeCreationPhase(StrEnum):
    # Reported by the session layer, which asks a model for the name before
    # the repository is touched.
    NAMING = "naming"
    FETCHING = "fetching"
    CHECKING_OUT = "checking_out"


@dataclass(frozen=True, slots=True)
class WorktreeCreationProgress:
    phase: WorktreeCreationPhase
    # Set only while checking out, and only once git reports a count: it holds
    # its progress back for the first seconds, so a fast checkout reports none.
    completed_files: int | None = None
    total_files: int | None = None


# Called on whichever thread is doing the work, which for everything past
# naming is a worker thread rather than the event loop.
type WorktreeProgressCallback = Callable[[WorktreeCreationProgress], None]
