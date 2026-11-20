from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server._local_projects import LocalProjectStore
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import ServerWarning
from vibe.app_server.models import PublicSessionProject
from vibe.app_server.protocol import ClientCapabilities, SessionOptions
from vibe.app_server.session import AppServerSession


def _call(call_id: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
    return {
        "id": call_id,
        "index": 0,
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


async def _settled_cwd(session: AppServerSession, previous: Path) -> Path:
    async with asyncio.timeout(5):
        while (cwd := Path(session.state.session.cwd or "")) == previous:
            await asyncio.sleep(0.01)
    return cwd


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_the_agent_moves_its_session_to_a_project_when_its_turn_ends(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: A session in a folder no project claims, and a project elsewhere.
    *Do*: The agent lists projects, moves to one, then runs a command in the same turn.
    *Assert*: The command runs where the turn started; once the turn ends the
    session runs in the project's folder, is stamped with it, and keeps its
    first folder reachable.
    """
    # Prepare
    scratch = (tmp_path / "scratch").resolve()
    folder = (tmp_path / "repo").resolve()
    scratch.mkdir()
    folder.mkdir()
    project = LocalProjectStore().create(name="repo", root_path=folder)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "", tool_calls=[_call("list-1", "list_projects", {})]
            ),
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project.id})
                ],
            ),
            backend_contract_mistral_response(
                "", tool_calls=[_call("bash-1", "bash", {"command": "touch during"})]
            ),
            backend_contract_mistral_response("moving"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(scratch),
            workspace_roots=[str(scratch)],
            enabled_tools=["bash", "list_projects", "move_to_project"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(agent_projects=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()

        # Do
        _ = [event async for event in session.act("work on my repo")]

        # Assert
        assert await _settled_cwd(session, scratch) == folder
        assert (scratch / "during").exists()
        async with asyncio.timeout(5):
            while session.state.session.project is None:
                await asyncio.sleep(0.01)
        assert session.state.session.project == PublicSessionProject(
            kind="local", id=project.id
        )
        assert opened_backends[-1]._adapter_config.workspace.cwd == folder
        assert scratch in opened_backends[-1]._adapter_config.workspace.roots
    finally:
        await connection.host.close()


async def _connect(scratch: Path, process_services: ProcessServices):
    return await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(scratch),
            workspace_roots=[str(scratch)],
            enabled_tools=["move_to_project"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(agent_projects=True),
        process_services=process_services,
    )


def _two_folders(tmp_path: Path) -> tuple[Path, Path, str]:
    scratch = (tmp_path / "scratch").resolve()
    folder = (tmp_path / "repo").resolve()
    scratch.mkdir()
    folder.mkdir()
    return scratch, folder, LocalProjectStore().create(name="repo", root_path=folder).id


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_stopping_the_turn_cancels_the_move_it_asked_for(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: The agent asks to move, then keeps working.
    *Do*: The user stops the turn.
    *Assert*: The session stays in its folder and in no project.
    """
    # Prepare
    scratch, _, project_id = _two_folders(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project_id})
                ],
            ),
            backend_contract_gated_mistral_response(
                "never", started=started, release=release
            ),
        ]
    )
    connection = await _connect(scratch, process_services)
    try:
        session = await connection.host.open_session()
        acting = asyncio.create_task(_drain(session.act("work on my repo")))
        try:
            await asyncio.wait_for(started.wait(), timeout=10)

            # Do
            await session.interrupt()
        finally:
            release.set()
        await acting
        await asyncio.sleep(1)

        # Assert
        assert Path(session.state.session.cwd or "") == scratch
        assert session.state.session.project is None
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_queued_turn_does_not_lose_the_move(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: The agent asks to move, and the user queues a message meanwhile.
    *Do*: Let both turns run.
    *Assert*: The session ends up in the project's folder.
    """
    # Prepare
    scratch, folder, project_id = _two_folders(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project_id})
                ],
            ),
            backend_contract_gated_mistral_response(
                "moving", started=started, release=release
            ),
            backend_contract_mistral_response("queued answer"),
        ]
    )
    connection = await _connect(scratch, process_services)
    try:
        session = await connection.host.open_session()
        acting = asyncio.create_task(_drain(session.act("work on my repo")))
        await asyncio.wait_for(started.wait(), timeout=10)
        await session.enqueue("and then this")

        # Do
        release.set()
        await acting

        # Assert
        async with asyncio.timeout(10):
            while Path(session.state.session.cwd or "") != folder:
                await asyncio.sleep(0.02)
        assert len(backend_contract_mistral_api.calls) == 3
    finally:
        await connection.host.close()


@pytest.mark.one_path
@pytest.mark.asyncio
async def test_a_folder_holding_the_project_is_not_kept(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: A session in a folder that holds the project's folder.
    *Do*: The agent moves to the project.
    *Assert*: The session's workspace narrows to the project: the outer folder is not kept.
    """
    # Prepare
    outer = tmp_path.resolve()
    folder = outer / "repo"
    folder.mkdir()
    project = LocalProjectStore().create(name="repo", root_path=folder)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project.id})
                ],
            ),
            backend_contract_mistral_response("moving"),
        ]
    )
    connection = await _connect(outer, process_services)
    try:
        session = await connection.host.open_session()

        # Do
        _ = [event async for event in session.act("work on my repo")]

        # Assert
        assert await _settled_cwd(session, outer) == folder
        # The stamp lands after the relocated context is adopted.
        async with asyncio.timeout(5):
            while session.state.session.project is None:
                await asyncio.sleep(0.01)
        assert opened_backends[-1]._adapter_config.workspace.cwd == folder
        assert outer not in opened_backends[-1]._adapter_config.workspace.roots
    finally:
        await connection.host.close()


async def _drain(events: AsyncIterator[object]) -> None:
    async for _ in events:
        pass


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_background_process_drops_the_move_with_a_notice(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
    opened_backends: list[Any],
) -> None:
    """*Prepare*: The agent starts a background process, then asks to move.
    *Do*: Let the turn end.
    *Assert*: The session stays, tells the user why, and keeps no move for later.
    """
    # Prepare
    scratch, _, project_id = _two_folders(tmp_path)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call(
                        "start-1",
                        "run_typescript",
                        {
                            "code": "async function main() { return "
                            "tools.process.start({command: 'sleep 30'}); }"
                        },
                    )
                ],
            ),
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project_id})
                ],
            ),
            backend_contract_mistral_response("moving"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(scratch), workspace_roots=[str(scratch)], auto_approve=True
        ),
        capabilities=ClientCapabilities(agent_projects=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        warned = asyncio.create_task(_next_warning(session, "background process"))

        # Do
        _ = [event async for event in session.act("start a server, then my repo")]

        # Assert
        await asyncio.wait_for(warned, timeout=5)
        assert Path(session.state.session.cwd or "") == scratch
        assert opened_backends[-1]._parked_project_move is None
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_message_sent_as_the_move_lands_runs_in_the_project(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: The agent asks to move.
    *Do*: The user writes again the moment the turn ends.
    *Assert*: The new turn is accepted and runs in the project's folder.
    """
    # Prepare
    scratch, folder, project_id = _two_folders(tmp_path)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project_id})
                ],
            ),
            backend_contract_mistral_response("moving"),
            backend_contract_mistral_response(
                "", tool_calls=[_call("bash-1", "bash", {"command": "touch after"})]
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(scratch),
            workspace_roots=[str(scratch)],
            enabled_tools=["bash", "move_to_project"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(agent_projects=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("work on my repo")]

        # Do
        _ = [event async for event in session.act("now touch a file")]

        # Assert
        assert (folder / "after").exists()
        assert not (scratch / "after").exists()
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_session_in_no_project_cancels_its_planned_move(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session in no project whose agent asks to move.
    *Do*: The agent cancels the move in the same turn.
    *Assert*: The session stays in its folder and in no project.
    """
    # Prepare
    scratch, _, project_id = _two_folders(tmp_path)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project_id})
                ],
            ),
            backend_contract_mistral_response(
                "", tool_calls=[_call("move-2", "move_to_project", {})]
            ),
            backend_contract_mistral_response("staying"),
        ]
    )
    connection = await _connect(scratch, process_services)
    try:
        session = await connection.host.open_session()

        # Do
        _ = [event async for event in session.act("actually, stay here")]
        await asyncio.sleep(1)

        # Assert
        results = [
            json.loads(call.request.content)["messages"][-1]["content"]
            for call in backend_contract_mistral_api.calls[1:]
        ]
        assert "cancelled" in results[-1]
        assert Path(session.state.session.cwd or "") == scratch
        assert session.state.session.project is None
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_closing_as_the_move_lands_keeps_the_whole_move(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: The agent asks to move.
    *Do*: The client closes the session the moment the turn ends.
    *Assert*: The resumed session is in the project's folder and stamped with it.
    """
    # Prepare
    scratch, folder, project_id = _two_folders(tmp_path)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _call("move-1", "move_to_project", {"project_id": project_id})
                ],
            ),
            backend_contract_mistral_response("moving"),
        ]
    )
    connection = await _connect(scratch, process_services)
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("work on my repo")]

        # Do
        await session.close()
    finally:
        await connection.host.close()

    # Assert
    resumed_connection = await _connect(scratch, process_services)
    try:
        resumed = await resumed_connection.host.resume_session(session.session_id)
        assert Path(resumed.state.session.cwd or "") == folder
        assert resumed.state.session.project == PublicSessionProject(
            kind="local", id=project_id
        )
    finally:
        await resumed_connection.host.close()


async def _next_warning(session: AppServerSession, text: str) -> None:
    async for event in session.events():
        if isinstance(event, ServerWarning) and text in event.params.warning.message:
            return
