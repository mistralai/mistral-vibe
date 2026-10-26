from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast

import pytest

from vibe.app_server._vibe_code import VibeCodeController, VibeCodeSession
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


class _SessionBusy(Exception):
    pass


class _FakeVibeCodeSession:
    def __init__(self, tmp_path: Path) -> None:
        self.turn_running = False
        self.rewriting = False
        self.remote_project_outcomes: list[str] = []
        self.cwd = tmp_path
        self.session_id = "session"
        self.config = SimpleNamespace(
            resolve_mistral_api_key=lambda: "key",
            vibe_code_sessions_base_url="https://vibe.example",
            api_timeout=1.0,
            is_active_model_mistral=lambda: True,
        )
        self.telemetry_client = SimpleNamespace(
            send_remote_project_configured=self._record_outcome,
            send_teleport_failed=lambda **_: None,
        )

    def _record_outcome(self, *, outcome: str, project_picker: object) -> None:
        self.remote_project_outcomes.append(outcome)

    def require_no_session_rewrite(self) -> None:
        if self.rewriting:
            raise _SessionBusy("rewrite")

    def require_idle(self) -> None:
        self.require_no_session_rewrite()
        if self.turn_running:
            raise _SessionBusy("turn")

    async def read_account(self) -> SimpleNamespace:
        return SimpleNamespace(teleport_eligible=True)

    def has_conversation_history(self) -> bool:
        return True


@pytest.fixture
def vibe_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[VibeCodeController, _FakeVibeCodeSession]:
    linked = _project("linked", "Vibe team")
    initial = _initial(tmp_path, [linked], saved_link=_link(tmp_path, linked))

    class FakePickerService:
        def __init__(self, **_: object) -> None:
            pass

        async def load_initial(self, _git: GitRepoInfo) -> object:
            return initial

        async def load_initial_for_teleport(self, _git: GitRepoInfo) -> object:
            return initial

        def resolve_project_for_teleport(self, data: object) -> SimpleNamespace:
            return SimpleNamespace(
                initial_data=data, project_id=None, stale_link_cleared=False
            )

        def save_project_link(
            self, *, context: object, project_id: str, project_name: str
        ) -> RemoteProjectLink:
            return RemoteProjectLink(
                repo_root=tmp_path,
                repo_url=REPO_URL,
                project_id=project_id,
                project_name=project_name,
            )

        def clear_project_link(self, _context: object) -> None:
            return None

    class FakeGitRepository:
        def __init__(self, workdir: Path) -> None:
            pass

        async def __aenter__(self) -> FakeGitRepository:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get_info(self) -> GitRepoInfo:
            return _git_info(tmp_path)

    monkeypatch.setattr(
        "vibe.app_server._vibe_code.VibeCodeProjectPickerService", FakePickerService
    )
    monkeypatch.setattr("vibe.core.teleport.git.GitRepository", FakeGitRepository)

    async def notify(_method: str, _params: object) -> None:
        return None

    session = _FakeVibeCodeSession(tmp_path)
    return VibeCodeController(cast(VibeCodeSession, session), notify), session


@pytest.mark.asyncio
async def test_configure_picker_links_and_unlinks_while_a_turn_runs(
    vibe_code: tuple[VibeCodeController, _FakeVibeCodeSession],
) -> None:
    controller, session = vibe_code
    session.turn_running = True

    picker_id, _, _ = await controller.open(purpose="configure")
    view, project = await controller.select(picker_id=picker_id, project_id="linked")
    unlinked = await controller.unlink(picker_id)
    await controller.cancel_picker(picker_id)

    assert project.project_id == "linked"
    assert view.context.saved_link is not None
    assert unlinked.context.saved_link is None
    assert session.remote_project_outcomes == ["configured", "unlinked", "cancelled"]


@pytest.mark.asyncio
async def test_configure_picker_waits_for_a_session_rewrite(
    vibe_code: tuple[VibeCodeController, _FakeVibeCodeSession],
) -> None:
    controller, session = vibe_code
    picker_id, _, _ = await controller.open(purpose="configure")
    session.rewriting = True

    with pytest.raises(_SessionBusy):
        await controller.open(purpose="configure")
    with pytest.raises(_SessionBusy):
        await controller.select(picker_id=picker_id, project_id="linked")


@pytest.mark.asyncio
async def test_teleport_picker_requires_an_idle_session(
    vibe_code: tuple[VibeCodeController, _FakeVibeCodeSession],
) -> None:
    controller, session = vibe_code
    picker_id, _, _ = await controller.open(purpose="teleport")
    session.turn_running = True

    with pytest.raises(_SessionBusy):
        await controller.select(picker_id=picker_id, project_id="linked")
    with pytest.raises(_SessionBusy):
        await controller.open(purpose="teleport")
