from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

import pytest

from vibe.app_server.session import AppServerSession
from vibe.core.teleport.git import GitRepoInfo
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    ProjectRepository,
    RemoteProjectLink,
    VibeCodeProject,
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerState,
)

REPO_URL = "https://github.com/mistralai/mistral-vibe.git"


@pytest.mark.parametrize(
    "module",
    ["vibe.app_server._host", "vibe.app_server._vibe_code", "vibe.app_server.server"],
)
def test_app_server_import_does_not_require_git_executable(
    tmp_path: Path, module: str
) -> None:
    env = os.environ.copy()
    env["GIT_PYTHON_GIT_EXECUTABLE"] = str(tmp_path / "missing-git")
    env["GIT_PYTHON_REFRESH"] = "raise"

    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def _project(
    project_id: str, name: str, repo_url: str = REPO_URL, *, read_only: bool = False
) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=(ProjectRepository(repo_url=repo_url),),
        is_read_only=read_only,
    )


def _link(tmp_path: Path, project: VibeCodeProject) -> RemoteProjectLink:
    return RemoteProjectLink(
        repo_root=tmp_path,
        repo_url=REPO_URL,
        project_id=project.project_id,
        project_name=project.name,
    )


def _initial(
    tmp_path: Path,
    projects: list[VibeCodeProject],
    *,
    saved_link: RemoteProjectLink | None = None,
    next_cursor: str | None = None,
) -> VibeCodeProjectPickerInitialData:
    return VibeCodeProjectPickerInitialData(
        context=ProjectPickerContext(
            repo_root=tmp_path,
            repo_url=REPO_URL,
            repo_name="mistral-vibe",
            saved_link=saved_link,
        ),
        state=VibeCodeProjectPickerState(
            projects=projects, next_cursor=next_cursor, repo_url=REPO_URL
        ),
    )


def _git_info(tmp_path: Path) -> GitRepoInfo:
    return GitRepoInfo(
        remote_name="origin",
        remote_url=REPO_URL,
        owner="mistralai",
        repo="mistral-vibe",
        branch="main",
        commit="abc123",
        diff="",
        default_branch="main",
        repo_root=tmp_path,
    )


def _server(session: AppServerSession) -> Any:
    client = session._connection.current
    assert client is not None
    peer = client._run_peer
    assert peer is not None
    return cast(Any, peer).__self__
