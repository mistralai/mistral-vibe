from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from git import Repo
import httpx
import pytest
import respx

from tests.agent_loop.e2e.conftest import build_e2e_agent_loop
from tests.constants import (
    CONNECTORS_BOOTSTRAP_PATH,
    MISTRAL_BASE_URL,
    TELEPORT_COMPLETE_URL,
    TELEPORT_SESSIONS_PATH,
)
from vibe.core.agent_loop import AgentLoop, TeleportError
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
)

# vibe_code_sessions_base_url is an exclude=True field, so it is dropped when the
# agent manager re-derives config via model_dump(); the default url is unavoidable.
SESSIONS_BASE_URL = "https://chat.mistral.ai"
SESSIONS_URL = f"{SESSIONS_BASE_URL}{TELEPORT_SESSIONS_PATH}"
GITHUB_REMOTE_URL = "https://github.com/owner/repo.git"


def _sessions_ok() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "sessionId": "controller-session-id",
            "webSessionId": "web-session-id",
            "projectId": "project-id",
            "status": "running",
            "url": TELEPORT_COMPLETE_URL,
        },
    )


def _commit(repo: Repo, message: str) -> str:
    (Path(repo.working_dir) / "file.txt").write_text(f"{message}\n")
    repo.git.add("file.txt")
    repo.git.commit("-m", message)
    return repo.head.commit.hexsha


def _init_repo(workdir: Path) -> Repo:
    # origin is intentionally not GitHub. The GitHub-looking `hub` remote is
    # rewritten to a local bare repo so fetch/push stay offline and instant.
    bare = Repo.init(workdir.with_name(f"{workdir.name}_origin.git"), bare=True)
    repo = Repo.init(workdir, initial_branch="work")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    repo.create_remote("origin", str(bare.git_dir))
    repo.git.config(f"url.{bare.git_dir}.insteadOf", GITHUB_REMOTE_URL)
    repo.create_remote("hub", GITHUB_REMOTE_URL)
    return repo


def _repo_with_pushed_branch(workdir: Path) -> Repo:
    repo = _init_repo(workdir)
    _commit(repo, "initial")
    repo.git.push("hub", "work")
    return repo


@pytest.fixture
def mock_sessions() -> Iterator[respx.MockRouter]:
    # One router stubs both hosts: the connector bootstrap (api.mistral.ai, hit on
    # agent init) and the teleport sessions endpoint (chat.mistral.ai).
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
            return_value=httpx.Response(200, json={"connectors": []})
        )
        router.post(SESSIONS_URL).mock(return_value=_sessions_ok())
        yield router


async def _drain(
    agent: AgentLoop, prompt: str | None, *, approve: bool | None = None
) -> list[object]:
    gen = agent.teleport_to_vibe_code(prompt, project_id="project-id")
    events: list[object] = []
    response: TeleportPushResponseEvent | None = None
    while True:
        try:
            event = await gen.asend(response)
        except StopAsyncIteration:
            break
        events.append(event)
        response = None
        if isinstance(event, TeleportPushRequiredEvent) and approve is not None:
            response = TeleportPushResponseEvent(approved=approve)
    return events


@pytest.mark.asyncio
async def test_teleport_completes_when_branch_already_pushed(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    _repo_with_pushed_branch(tmp_working_directory)

    events = await _drain(build_e2e_agent_loop(), "do the thing")

    assert [type(e) for e in events] == [
        TeleportCheckingGitEvent,
        TeleportStartingWorkflowEvent,
        TeleportCompleteEvent,
    ]
    assert isinstance(events[-1], TeleportCompleteEvent)
    assert events[-1].url == TELEPORT_COMPLETE_URL


@pytest.mark.asyncio
async def test_teleport_sends_repo_metadata_and_diff(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    repo = _repo_with_pushed_branch(tmp_working_directory)
    commit = repo.head.commit.hexsha
    (tmp_working_directory / "file.txt").write_text("uncommitted change\n")

    await _drain(build_e2e_agent_loop(), "ship it")

    payload = mock_sessions.calls.last.request.read().decode()
    assert '"projectId":"project-id"' in payload
    assert "https://github.com/owner/repo.git" in payload
    assert "work" in payload
    assert commit in payload
    assert "zstd" in payload


@pytest.mark.asyncio
async def test_teleport_pushes_then_completes_when_approved(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    repo = _repo_with_pushed_branch(tmp_working_directory)
    head = _commit(repo, "second")

    events = await _drain(build_e2e_agent_loop(), "ship it", approve=True)

    assert [type(e) for e in events] == [
        TeleportCheckingGitEvent,
        TeleportPushRequiredEvent,
        TeleportPushingEvent,
        TeleportStartingWorkflowEvent,
        TeleportCompleteEvent,
    ]
    assert repo.remote("hub").refs["work"].commit.hexsha == head


@pytest.mark.asyncio
async def test_teleport_aborts_when_push_declined(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    repo = _repo_with_pushed_branch(tmp_working_directory)
    _commit(repo, "second")

    with pytest.raises(TeleportError, match="not pushed"):
        await _drain(build_e2e_agent_loop(), "ship it", approve=False)

    assert not mock_sessions.post(SESSIONS_URL).called


@pytest.mark.asyncio
async def test_teleport_fails_when_push_fails(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    repo = _repo_with_pushed_branch(tmp_working_directory)
    _commit(repo, "second")
    repo.git.remote("set-url", "--push", "hub", "/nonexistent/repo.git")

    with pytest.raises(TeleportError, match="Failed to push"):
        await _drain(build_e2e_agent_loop(), "ship it", approve=True)


@pytest.mark.asyncio
async def test_teleport_requires_a_branch(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    repo = _repo_with_pushed_branch(tmp_working_directory)
    repo.git.checkout(repo.head.commit.hexsha)  # detach HEAD

    with pytest.raises(TeleportError, match="checked-out branch"):
        await _drain(build_e2e_agent_loop(), "ship it")


@pytest.mark.asyncio
async def test_teleport_rejects_empty_prompt(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    repo = _init_repo(tmp_working_directory)
    _commit(repo, "initial")

    with pytest.raises(TeleportError, match="non-empty prompt"):
        await _drain(build_e2e_agent_loop(), None)


@pytest.mark.asyncio
async def test_teleport_surfaces_http_error(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    _repo_with_pushed_branch(tmp_working_directory)
    mock_sessions.post(SESSIONS_URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(TeleportError, match="start failed"):
        await _drain(build_e2e_agent_loop(), "ship it")


@pytest.mark.asyncio
async def test_teleport_unsupported_without_github_remote(
    tmp_working_directory: Path, mock_sessions: respx.MockRouter
) -> None:
    repo = Repo.init(tmp_working_directory, initial_branch="work")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    _commit(repo, "initial")

    with pytest.raises(TeleportError, match="GitHub"):
        await _drain(build_e2e_agent_loop(), "ship it")
