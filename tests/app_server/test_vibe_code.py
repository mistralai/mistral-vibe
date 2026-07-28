from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_account_gateway import FakeAccountGateway
from vibe.app_server._account import WhoAmIResult
from vibe.app_server._vibe_code import VibeCodeController
from vibe.app_server.models import (
    AccountPlanKind,
    VibeCodeProject as PublicVibeCodeProject,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolErrorCode,
    VibeCodeProjectsLoadMoreParams,
)
from vibe.app_server.session import AppServerSession
from vibe.core.agent_loop import AgentLoop, TeleportError
from vibe.core.teleport.git import GitRepoInfo
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
)
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    ProjectRepository,
    TeleportProjectResolution,
    VibeCodeProject,
    VibeCodeProjectCreateResult,
    VibeCodeProjectLink,
    VibeCodeProjectLoadMoreResult,
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
)

REPO_URL = "https://github.com/mistralai/mistral-vibe.git"


def test_app_server_import_does_not_require_git_executable(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["GIT_PYTHON_GIT_EXECUTABLE"] = str(tmp_path / "missing-git")
    env["GIT_PYTHON_REFRESH"] = "raise"

    result = subprocess.run(
        [sys.executable, "-c", "import vibe.app_server._vibe_code"],
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


class FakePickerService:
    def __init__(
        self,
        initial: VibeCodeProjectPickerInitialData,
        *,
        recovery: VibeCodeProjectPickerInitialData | None = None,
    ) -> None:
        self.initial = initial
        self.recovery = recovery or initial
        self.load_initial_calls = 0
        self.load_more_calls = 0
        self.saved_links: list[VibeCodeProjectLink] = []
        self.cleared_contexts: list[ProjectPickerContext] = []

    async def load_initial(
        self, _git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        self.load_initial_calls += 1
        return self.recovery

    async def load_initial_for_teleport(
        self, _git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        return self.initial

    async def load_more(
        self, state: VibeCodeProjectPickerState
    ) -> VibeCodeProjectLoadMoreResult:
        self.load_more_calls += 1
        return VibeCodeProjectLoadMoreResult(state=state, focus_project_id=None)

    async def create_project(
        self,
        *,
        name: str,
        default_branch: str,
        git_info: GitRepoInfo,
        state: VibeCodeProjectPickerState,
    ) -> VibeCodeProjectCreateResult:
        project = _project("created", name, git_info.remote_url)
        project = VibeCodeProject(
            project_id=project.project_id,
            name=project.name,
            repositories=(
                ProjectRepository(
                    repo_url=git_info.remote_url, default_branch=default_branch
                ),
            ),
        )
        return VibeCodeProjectCreateResult(
            state=VibeCodeProjectPickerState(
                projects=[project, *state.projects],
                next_cursor=state.next_cursor,
                repo_url=state.repo_url,
            ),
            project=project,
        )

    def save_project_link(
        self, *, context: ProjectPickerContext, project_id: str, project_name: str
    ) -> VibeCodeProjectLink:
        link = VibeCodeProjectLink(
            repo_root=context.repo_root,
            repo_url=context.repo_url,
            project_id=project_id,
            project_name=project_name,
        )
        self.saved_links.append(link)
        return link

    def clear_project_link(self, context: ProjectPickerContext) -> None:
        self.cleared_contexts.append(context)

    def resolve_project_for_teleport(
        self, initial: VibeCodeProjectPickerInitialData
    ) -> TeleportProjectResolution:
        link = initial.context.saved_link
        return TeleportProjectResolution(
            project_id=link.project_id if link is not None else None,
            initial_data=initial,
            stale_link_cleared=False,
        )


def _project(
    project_id: str, name: str, repo_url: str = REPO_URL, *, read_only: bool = False
) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=(ProjectRepository(repo_url=repo_url),),
        is_read_only=read_only,
    )


def _link(tmp_path: Path, project: VibeCodeProject) -> VibeCodeProjectLink:
    return VibeCodeProjectLink(
        repo_root=tmp_path,
        repo_url=REPO_URL,
        project_id=project.project_id,
        project_name=project.name,
    )


def _initial(
    tmp_path: Path,
    projects: list[VibeCodeProject],
    *,
    saved_link: VibeCodeProjectLink | None = None,
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


async def _make_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, service: FakePickerService
) -> tuple[AgentLoop, AppServerSession]:
    monkeypatch.setattr(
        VibeCodeController,
        "_make_service",
        lambda _self: cast(VibeCodeProjectPickerService, service),
    )

    async def read_git(_self: VibeCodeController) -> GitRepoInfo:
        return _git_info(tmp_path)

    monkeypatch.setattr(VibeCodeController, "_read_git", read_git)
    agent_loop = build_test_agent_loop()
    account_gateway = FakeAccountGateway(
        WhoAmIResult(
            plan_type=AccountPlanKind.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=False,
        )
    )
    return agent_loop, await create_test_app_server_session(
        agent_loop, account_gateway=account_gateway
    )


def _server(session: AppServerSession) -> Any:
    client = session._connection.current
    assert client is not None
    peer = client._run_peer
    assert peer is not None
    return cast(Any, peer).__self__


@pytest.mark.asyncio
async def test_teleport_preconditions_are_server_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    telemetry_events: list[dict[str, Any]],
) -> None:
    service = FakePickerService(_initial(tmp_path, []))
    _, session = await _make_session(monkeypatch, tmp_path, service)

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await session.resources.vibe_code.open_projects(for_teleport=True)
    finally:
        await session.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert str(exc_info.value) == "No conversation history to teleport."
    failures = [
        event
        for event in telemetry_events
        if event.get("event_name") == "vibe.teleport_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["properties"]["stage"] == "no_history"
    assert failures[0]["properties"]["error_class"] == "TeleportNoHistoryError"
    assert service.load_initial_calls == 0


@pytest.mark.asyncio
async def test_picker_id_rejects_stale_requests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = FakePickerService(
        _initial(tmp_path, [_project("project-1", "Canonical")], next_cursor="next")
    )
    _, session = await _make_session(monkeypatch, tmp_path, service)

    try:
        await session.resources.vibe_code.open_projects()
        stale_picker_id = session.resources.vibe_code._picker_id
        await session.resources.vibe_code.open_projects()
        current_picker_id = session.resources.vibe_code._picker_id
        client = await session._connection.connect()

        assert stale_picker_id is not None
        assert current_picker_id is not None
        assert stale_picker_id != current_picker_id
        assert stale_picker_id not in {"project-1", session.session_id}

        with pytest.raises(AppServerResponseError) as raised:
            await client.request(
                "vibeCode/projects/loadMore",
                VibeCodeProjectsLoadMoreParams(
                    session_id=session.session_id, picker_id=stale_picker_id
                ),
            )
    finally:
        await session.close()

    assert raised.value.error.code is ProtocolErrorCode.CONFLICT
    assert service.load_more_calls == 0


@pytest.mark.asyncio
async def test_select_validates_project_and_returns_canonical_mutation_views(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    canonical = _project("canonical", "Canonical project")
    read_only = _project("read-only", "Read only", read_only=True)
    unrelated = _project(
        "unrelated", "Unrelated", "https://github.com/mistralai/other.git"
    )
    service = FakePickerService(_initial(tmp_path, [canonical, read_only, unrelated]))
    _, session = await _make_session(monkeypatch, tmp_path, service)

    try:
        await session.resources.vibe_code.open_projects()

        for project_id in ("missing", read_only.project_id, unrelated.project_id):
            with pytest.raises(AppServerResponseError) as raised:
                await session.resources.vibe_code.select_project(project_id)
            assert raised.value.error.code is ProtocolErrorCode.INVALID_PARAMS

        selected_view, selected = await session.resources.vibe_code.select_project(
            canonical.project_id
        )
        unlinked_view = await session.resources.vibe_code.unlink()
    finally:
        await session.close()

    assert selected == _public_project(canonical)
    assert selected_view.context.saved_link is not None
    assert selected_view.context.saved_link.project_name == canonical.name
    assert service.saved_links[-1].project_name == canonical.name
    assert unlinked_view.context.saved_link is None
    assert unlinked_view.saved_project_link_cleared is True
    assert service.cleared_contexts[-1].saved_link is not None


@pytest.mark.asyncio
async def test_teleport_is_one_session_execution_and_conflicts_with_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project("project-1", "Canonical")
    service = FakePickerService(_initial(tmp_path, [project]))
    agent_loop, session = await _make_session(monkeypatch, tmp_path, service)
    release = asyncio.Event()
    closed = asyncio.Event()

    async def teleport(
        _prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: object | None = None,
    ) -> AsyncGenerator[object, TeleportPushResponseEvent | None]:
        assert project_id == project.project_id
        assert project_picker is not None
        try:
            yield TeleportCheckingGitEvent()
            await release.wait()
            yield TeleportCompleteEvent(url="https://example.test/session")
        finally:
            closed.set()

    monkeypatch.setattr(agent_loop, "teleport_to_vibe_code", teleport)
    stream = session.resources.vibe_code.teleport(None, project_id=project.project_id)
    second = session.resources.vibe_code.teleport(None, project_id=project.project_id)
    turn = session.act("race the teleport")

    try:
        await session.resources.vibe_code.open_projects(
            for_teleport=True, prompt="ship it"
        )
        await session.resources.vibe_code.select_project(project.project_id)
        await anext(stream)

        with pytest.raises(AppServerResponseError) as second_error:
            await anext(second)
        with pytest.raises(AppServerResponseError) as turn_error:
            await anext(turn)
    finally:
        await second.aclose()
        await turn.aclose()
        await stream.aclose()
        await session.close()

    assert second_error.value.error.code is ProtocolErrorCode.CONFLICT
    assert turn_error.value.error.code is ProtocolErrorCode.CONFLICT
    assert closed.is_set()


@pytest.mark.asyncio
async def test_teleport_response_precedes_progress_and_push_response_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project("project-1", "Canonical")
    service = FakePickerService(_initial(tmp_path, [project]))
    agent_loop, session = await _make_session(monkeypatch, tmp_path, service)
    push_responses: list[TeleportPushResponseEvent | None] = []
    wire_order: list[str] = []

    async def teleport(
        _prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: object | None = None,
    ) -> AsyncGenerator[object, TeleportPushResponseEvent | None]:
        assert project_id == project.project_id
        assert project_picker is not None
        yield TeleportCheckingGitEvent()
        response = yield TeleportPushRequiredEvent(
            unpushed_count=2, branch_not_pushed=True
        )
        push_responses.append(response)
        yield TeleportPushingEvent()
        yield TeleportStartingWorkflowEvent()
        yield TeleportCompleteEvent(url="https://example.test/session")

    monkeypatch.setattr(agent_loop, "teleport_to_vibe_code", teleport)

    try:
        await session.resources.vibe_code.open_projects(
            for_teleport=True, prompt="ship it"
        )
        await session.resources.vibe_code.select_project(project.project_id)
        server_transport = _server(session)._transport
        original_send = server_transport.send

        async def record_send(message: dict[str, Any]) -> None:
            if "id" in message and "result" in message:
                wire_order.append("response")
            elif message.get("method") == "vibeCode/teleport/event":
                params = message.get("params")
                if isinstance(params, dict):
                    event = params.get("event")
                    if isinstance(event, dict) and isinstance(event.get("kind"), str):
                        wire_order.append(event["kind"])
            await original_send(message)

        monkeypatch.setattr(server_transport, "send", record_send)
        stream = session.resources.vibe_code.teleport(
            "ship it", project_id=project.project_id
        )
        checking = await anext(stream)
        push_required = await anext(stream)
        await session.resources.vibe_code.respond_to_push(
            push_required.operation_id, approved=True
        )
        remaining = [event async for event in stream]
    finally:
        await session.close()

    assert checking.kind == "checking_git"
    assert push_required.kind == "push_required"
    assert push_responses == [TeleportPushResponseEvent(approved=True)]
    assert [event.kind for event in remaining] == [
        "pushing",
        "starting_workflow",
        "complete",
    ]
    assert wire_order[:2] == ["response", "checking_git"]


@pytest.mark.asyncio
async def test_closing_teleport_stream_cancels_server_operation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project("project-1", "Canonical")
    service = FakePickerService(_initial(tmp_path, [project]))
    agent_loop, session = await _make_session(monkeypatch, tmp_path, service)
    closed = asyncio.Event()
    never = asyncio.Event()

    async def teleport(
        _prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: object | None = None,
    ) -> AsyncGenerator[object, TeleportPushResponseEvent | None]:
        try:
            yield TeleportCheckingGitEvent()
            await never.wait()
        finally:
            closed.set()

    monkeypatch.setattr(agent_loop, "teleport_to_vibe_code", teleport)
    stream = session.resources.vibe_code.teleport(None, project_id=project.project_id)
    server = _server(session)
    controller = server._handler._vibe_code

    try:
        await session.resources.vibe_code.open_projects(
            for_teleport=True, prompt="ship it"
        )
        await session.resources.vibe_code.select_project(project.project_id)
        await anext(stream)
        await stream.aclose()
        await asyncio.wait_for(closed.wait(), timeout=1)
    finally:
        await stream.aclose()
        await session.close()

    assert controller._tasks == {}
    assert controller._reserved_operations == {}


@pytest.mark.asyncio
async def test_terminal_failure_recovers_stale_link_with_canonical_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project("stale", "Stale project")
    initial = _initial(tmp_path, [], saved_link=_link(tmp_path, project))
    recovery = _initial(tmp_path, [_project("replacement", "Replacement")])
    service = FakePickerService(initial, recovery=recovery)
    agent_loop, session = await _make_session(monkeypatch, tmp_path, service)

    async def teleport(
        _prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: object | None = None,
    ) -> AsyncGenerator[object, TeleportPushResponseEvent | None]:
        if False:
            yield TeleportCheckingGitEvent()
        raise TeleportError("Project not found (status 404)")

    monkeypatch.setattr(agent_loop, "teleport_to_vibe_code", teleport)

    try:
        _, resolved_project_id = await session.resources.vibe_code.open_projects(
            for_teleport=True, prompt="ship it"
        )
        stream = session.resources.vibe_code.teleport(
            None, project_id=cast(str, resolved_project_id)
        )
        failed = await anext(stream)
        (
            recovered_view,
            recovered,
        ) = await session.resources.vibe_code.recover_stale_link()
        await stream.aclose()
    finally:
        await session.close()

    assert failed.kind == "failed"
    assert failed.error.code == "saved_project_stale"
    assert recovered is True
    assert recovered_view.context.saved_link is None
    assert [project.project_id for project in recovered_view.state.projects] == [
        "replacement"
    ]
    assert len(service.cleared_contexts) == 1


@pytest.mark.asyncio
async def test_shutdown_cleans_up_push_waiter_and_teleport_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = _project("project-1", "Canonical")
    service = FakePickerService(_initial(tmp_path, [project]))
    agent_loop, session = await _make_session(monkeypatch, tmp_path, service)
    closed = asyncio.Event()

    async def teleport(
        _prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: object | None = None,
    ) -> AsyncGenerator[object, TeleportPushResponseEvent | None]:
        try:
            yield TeleportPushRequiredEvent(unpushed_count=1)
        finally:
            closed.set()

    monkeypatch.setattr(agent_loop, "teleport_to_vibe_code", teleport)
    stream = session.resources.vibe_code.teleport(None, project_id=project.project_id)
    server = _server(session)
    controller = server._handler._vibe_code

    await session.resources.vibe_code.open_projects(for_teleport=True, prompt="ship it")
    await session.resources.vibe_code.select_project(project.project_id)
    push_required = await anext(stream)
    assert push_required.kind == "push_required"

    await session.close()
    await asyncio.wait_for(closed.wait(), timeout=1)
    with suppress(Exception):
        await stream.aclose()

    assert controller._tasks == {}
    assert controller._reserved_operations == {}
    assert controller._push_responses == {}


def _public_project(project: VibeCodeProject) -> PublicVibeCodeProject:
    return PublicVibeCodeProject.model_validate({
        "project_id": project.project_id,
        "name": project.name,
        "repositories": [
            {
                "repo_url": repository.repo_url,
                "default_branch": repository.default_branch,
            }
            for repository in project.repositories
        ],
        "is_read_only": project.is_read_only,
    })
