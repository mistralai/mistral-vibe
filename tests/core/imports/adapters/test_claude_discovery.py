from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from vibe.core.imports.adapters.claude_project import (
    ClaudeProject,
    ClaudeProjectSource,
    ProjectDirectoryStatus,
)
from vibe.core.imports.adapters.claude_session import (
    ClaudeSessionSource,
    ClaudeSessionSummary,
)


def test_claude_project_fast_discovery_reports_every_configuration_project(
    tmp_path: Path,
) -> None:
    existing_project_path = tmp_path / "existing-project"
    existing_project_path.mkdir()
    missing_project_path = tmp_path / "missing-project"
    config_path = tmp_path / ".claude.json"
    config_path.write_text(
        json.dumps({
            "projects": {
                str(missing_project_path): {"hasTrustDialogAccepted": True},
                str(existing_project_path): {"hasTrustDialogAccepted": True},
            }
        }),
        encoding="utf-8",
    )

    projects = ClaudeProjectSource(config_path).discover()

    assert projects == [
        ClaudeProject(
            name="existing-project",
            cwd=existing_project_path,
            directory_status=ProjectDirectoryStatus.DIRECTORY_EXISTS,
        ),
        ClaudeProject(
            name="missing-project",
            cwd=missing_project_path,
            directory_status=ProjectDirectoryStatus.NO_DIRECTORY,
        ),
    ]


def test_claude_session_fast_discovery_lists_files_without_opening_them(
    tmp_path: Path,
) -> None:
    projects_path = tmp_path / "projects"
    transcript_directory = projects_path / "encoded-project"
    transcript_directory.mkdir(parents=True)
    project_path = tmp_path / "project"
    project_path.mkdir()
    session_id = UUID("11111111-1111-1111-1111-111111111111")
    other_session_id = UUID("22222222-2222-2222-2222-222222222222")
    session_path = transcript_directory / f"{session_id}.jsonl"
    session_path.write_text("not JSON\n", encoding="utf-8")
    (transcript_directory / f"{other_session_id}.jsonl").write_text(
        "also not JSON\n", encoding="utf-8"
    )
    (transcript_directory / "not-a-session.jsonl").write_text(
        '{"cwd":"' + str(project_path) + '"}\n', encoding="utf-8"
    )
    sessions = ClaudeSessionSource(projects_path).discover()

    assert sessions == [
        ClaudeSessionSummary(id=session_id, path=session_path),
        ClaudeSessionSummary(
            id=other_session_id, path=transcript_directory / f"{other_session_id}.jsonl"
        ),
    ]
