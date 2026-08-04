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
    from vibe.core.vibe_code_project.picker_service import HeadlessProjectResolution


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


def build_headless_project_telemetry(
    resolution: HeadlessProjectResolution,
) -> ProjectPickerTelemetryPayload:
    return {
        "project_picker_shown": False,
        "project_selection_source": resolution.source,
        "project_candidate_count_loaded": resolution.candidate_count_loaded,
        "project_multi_repo_match_count": resolution.multi_repo_match_count,
        "saved_project_link_cleared": resolution.saved_project_link_cleared,
        "project_repo_remote_changed": resolution.project_repo_remote_changed,
    }


def build_project_resolution_failed_telemetry() -> ProjectPickerTelemetryPayload:
    return {
        "project_picker_shown": False,
        "project_selection_source": "cancelled",
        "project_candidate_count_loaded": 0,
        "project_multi_repo_match_count": 0,
        "saved_project_link_cleared": False,
        "project_repo_remote_changed": False,
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
