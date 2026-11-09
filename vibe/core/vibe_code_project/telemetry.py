from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from vibe.core.telemetry.types import (
    ProjectPickerTelemetryPayload,
    ProjectSelectionSource,
)
from vibe.core.vibe_code_project.selection import (
    VibeCodeProject,
    is_project_linked_to_repo,
)

if TYPE_CHECKING:
    pass


def build_project_picker_telemetry(
    *,
    source: ProjectSelectionSource,
    shown: bool,
    projects: Sequence[VibeCodeProject],
    repo_url: str,
    saved_project_link_cleared: bool = False,
    project_repo_remote_changed: bool = False,
) -> ProjectPickerTelemetryPayload:
    return {
        "project_picker_shown": shown,
        "project_selection_source": source,
        "project_candidate_count_loaded": len(projects),
        "project_multi_repo_match_count": count_multi_repo_matches(projects, repo_url),
        "saved_project_link_cleared": saved_project_link_cleared,
        "project_repo_remote_changed": project_repo_remote_changed,
    }


def count_multi_repo_matches(projects: Sequence[VibeCodeProject], repo_url: str) -> int:
    if not repo_url:
        return 0
    return sum(
        1
        for project in projects
        if len(project.repositories) > 1
        and is_project_linked_to_repo(project, repo_url)
    )
