from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
import json
from pathlib import Path

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.local import LocalHarnessHost, LocalHarnessOptions
from vibe.app_server.models import PublicMessageEntry
from vibe.app_server.protocol import (
    ClientCapabilities,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
)
from vibe.app_server.session import AppServerSession

pytestmark = pytest.mark.both_paths


@pytest.mark.asyncio
async def test_session_start_returns_the_attached_public_session(
    backend_contract_session: AppServerSession,
) -> None:
    assert (
        backend_contract_session.state.session.id == backend_contract_session.session_id
    )


@pytest.mark.asyncio
async def test_unified_session_advertises_only_supported_agent_tools(
    backend_contract_mistral_api: respx.Route,
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A local Unified Harness session and scripted provider chunks.
    *Do*: Start a turn and discover the agent tool declarations.
    *Assert*: Local child tools are available without conversation forks; waiting
              is the standalone direct ``wait`` tool, not an ``agents`` function.
    """
    # Prepare
    # Accounting is covered separately; every reply still reports its usage.
    choices = [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "delta": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "discover-agent-tools",
                        "index": 0,
                        "function": {
                            "name": "search_tool_functions",
                            "arguments": json.dumps({
                                "mode": "details",
                                "functions": [
                                    "agents.list",
                                    "agents.spawn",
                                    "agents.send_message",
                                    "agents.interrupt",
                                ],
                            }),
                        },
                    }
                ],
            },
        },
        {
            "index": 0,
            "finish_reason": "stop",
            "delta": {"role": "assistant", "content": "Local agent tools are ready."},
        },
    ]
    backend_contract_mistral_api.mock(
        side_effect=[
            httpx.Response(
                200,
                content="data: "
                + json.dumps({
                    "id": "completion-agent-tools",
                    "object": "chat.completion.chunk",
                    "created": 1234567890,
                    "model": "mistral-vibe-cli-latest",
                    "choices": [choice],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                })
                + "\n\ndata: [DONE]\n",
                headers={"Content-Type": "text/event-stream"},
            )
            for choice in choices
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(cwd=str(tmp_path)),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        # Do
        session = await connection.host.open_session()
        _ = [event async for event in session.act("Show the local agent tools")]
    finally:
        await connection.host.close()

    # Assert
    first_request, second_request = [
        json.loads(call.request.content) for call in backend_contract_mistral_api.calls
    ]
    direct_tools = {tool["function"]["name"] for tool in first_request["tools"]}
    assert {"search_tool_functions", "run_typescript", "wait"} <= direct_tools
    declarations = next(
        message["content"]
        for message in second_request["messages"]
        if message.get("tool_call_id") == "discover-agent-tools"
    )
    assert "declare namespace tools.agents" in declarations
    for operation in ("list", "spawn", "send_message", "interrupt"):
        assert f"function {operation}(" in declarations
    assert "context?:" not in declarations
    assert "function wait(" not in declarations


@pytest.mark.asyncio
async def test_session_read_returns_the_public_snapshot_and_event_watermark(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    result = await backend_contract_connection.client.request(
        "session/read",
        SessionReadParams(session_id=backend_contract_session.session_id),
    )

    response = SessionReadResponse.model_validate(result)
    assert response.state.session.id == backend_contract_session.session_id
    assert "vibe" not in result
    assert "state" in result
    assert result["lastEventId"] == response.state.event_id
    assert "latestTurn" not in result["state"]
    assert isinstance(result["state"]["history"], list)
    assert isinstance(result["state"]["turns"], list)


@pytest.mark.asyncio
async def test_fork_attaches_the_public_child_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("First answer")
    )
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "First", client_message_id="user-1"
        )
    ]
    source_session_id = backend_contract_persistent_session.session_id

    fork = await backend_contract_persistent_session.resources.sessions.fork("user-1")

    assert fork.source_session_id == source_session_id
    assert backend_contract_persistent_session.session_id != source_session_id
    assert (
        backend_contract_persistent_session.state.session.parent_session_id
        == source_session_id
    )
    assert [
        entry.text
        for entry in backend_contract_persistent_session.history
        if isinstance(entry, PublicMessageEntry)
    ] == ["First", "First answer"]


@pytest.mark.asyncio
async def test_detached_fork_returns_a_public_child_without_rebinding_the_source(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("Second answer"),
        ]
    )
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "First", client_message_id="user-1"
        )
    ]
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "Second", client_message_id="user-2"
        )
    ]
    source_session_id = backend_contract_persistent_session.session_id

    fork = await backend_contract_persistent_session.resources.sessions.fork(
        "user-1", attach=False
    )

    assert backend_contract_persistent_session.session_id == source_session_id
    assert fork.source_session_id == source_session_id
    assert fork.state.session.id != source_session_id
    assert fork.state.session.parent_session_id == source_session_id
    assert [
        entry.text
        for entry in fork.state.history or []
        if isinstance(entry, PublicMessageEntry)
    ] == ["First", "First answer"]


@pytest.mark.asyncio
async def test_detached_fork_is_readable_and_resumable_from_a_new_host(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
    process_services: ProcessServices,
) -> None:
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("First answer")
    )
    _ = [
        event
        async for event in backend_contract_persistent_session.act(
            "First", client_message_id="user-1"
        )
    ]
    fork = await backend_contract_persistent_session.resources.sessions.fork(
        "user-1", attach=False
    )

    connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    resumed: AppServerSession | None = None
    try:
        stored = await connection.host.read_session(fork.state.session.id)
        resumed = await connection.host.resume_session(fork.state.session.id)
    finally:
        if resumed is None:
            await connection.host.close()
        else:
            await resumed.close()

    assert stored.session.id == fork.state.session.id
    assert (
        stored.session.parent_session_id
        == backend_contract_persistent_session.session_id
    )
    assert resumed is not None
    assert resumed.session_id == fork.state.session.id
    assert [
        entry.text for entry in resumed.history if isinstance(entry, PublicMessageEntry)
    ] == ["First", "First answer"]


def _texts(entries: list | None) -> list[str]:
    return [
        entry.text for entry in entries or [] if isinstance(entry, PublicMessageEntry)
    ]


async def _act(
    session: AppServerSession, prompt: str, client_message_id: str | None
) -> None:
    _ = [
        event
        async for event in session.act(prompt, client_message_id=client_message_id)
    ]


@contextlib.asynccontextmanager
async def _running_turn(
    session: AppServerSession,
    started: asyncio.Event,
    release: asyncio.Event,
    client_message_id: str | None = "user-running",
) -> AsyncIterator[asyncio.Task[None]]:
    turn = asyncio.create_task(_act(session, "Running", client_message_id))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        yield turn
    finally:
        release.set()
        await asyncio.wait_for(turn, timeout=5)


@pytest.mark.asyncio
async def test_fork_during_a_turn_copies_the_session_before_that_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_gated_mistral_response(
                "Running answer", started=started, release=release
            ),
        ]
    )
    session = backend_contract_persistent_session
    await _act(session, "First", "user-1")

    async with _running_turn(session, started, release) as turn:
        fork = await session.resources.sessions.fork(attach=False)
        running = not turn.done()

    assert running
    assert _texts(fork.state.history) == ["First", "First answer"]
    assert _texts(session.history) == [
        "First",
        "First answer",
        "Running",
        "Running answer",
    ]


@pytest.mark.asyncio
async def test_fork_during_a_steered_turn_copies_the_session_before_that_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    steered, release_steered = asyncio.Event(), asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_gated_mistral_response(
                "Before steer", started=started, release=release
            ),
            backend_contract_gated_mistral_response(
                "After steer", started=steered, release=release_steered
            ),
        ]
    )
    session = backend_contract_persistent_session
    await _act(session, "First", "user-1")

    async with _running_turn(session, started, release_steered):
        await session.inject_user_context(
            "Steer", as_message=True, client_message_id="user-steer"
        )
        release.set()
        await asyncio.wait_for(steered.wait(), timeout=5)
        fork = await session.resources.sessions.fork(attach=False)

    assert _texts(fork.state.history) == ["First", "First answer"]


@pytest.mark.asyncio
async def test_fork_during_a_turn_without_a_client_message_id_copies_the_session_before_it(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_gated_mistral_response(
                "Running answer", started=started, release=release
            ),
        ]
    )
    session = backend_contract_persistent_session
    await _act(session, "First", "user-1")

    async with _running_turn(session, started, release, client_message_id=None):
        fork = await session.resources.sessions.fork(attach=False)

    assert _texts(fork.state.history) == ["First", "First answer"]


@pytest.mark.asyncio
async def test_fork_during_a_steered_injected_turn_copies_the_session_before_it(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    steered, release_steered = asyncio.Event(), asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_gated_mistral_response(
                "Before steer", started=started, release=release
            ),
            backend_contract_gated_mistral_response(
                "After steer", started=steered, release=release_steered
            ),
        ]
    )
    session = backend_contract_persistent_session
    await _act(session, "First", "user-1")

    async def retry() -> None:
        _ = [
            event
            async for event in session.act(
                "Retry", client_message_id="user-retry", injected=True
            )
        ]

    turn = asyncio.create_task(retry())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await session.inject_user_context(
            "Steer", as_message=True, client_message_id="user-steer"
        )
        release.set()
        await asyncio.wait_for(steered.wait(), timeout=5)
        fork = await session.resources.sessions.fork(attach=False)
    finally:
        release_steered.set()
        await asyncio.wait_for(turn, timeout=5)

    assert _texts(fork.state.history) == ["First", "First answer"]


@pytest.mark.asyncio
async def test_fork_during_the_first_turn_copies_an_empty_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "Running answer", started=started, release=release
        )
    )
    session = backend_contract_persistent_session

    async with _running_turn(session, started, release):
        fork = await session.resources.sessions.fork(attach=False)

    assert fork.state.session.parent_session_id == session.session_id
    assert _texts(fork.state.history) == []


@pytest.mark.asyncio
async def test_fork_during_a_turn_after_a_compaction_copies_the_turns_before_it(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    started, release = asyncio.Event(), asyncio.Event()
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("First answer"),
            backend_contract_mistral_response("<summary>One turn</summary>"),
            backend_contract_mistral_response("Second answer"),
            backend_contract_gated_mistral_response(
                "Running answer", started=started, release=release
            ),
        ]
    )
    session = backend_contract_persistent_session
    await _act(session, "First", "user-1")
    await session.compact()
    await _act(session, "Second", "user-2")

    async with _running_turn(session, started, release):
        fork = await session.resources.sessions.fork(attach=False)

    copied = _texts(fork.state.history)
    assert copied[-2:] == ["Second", "Second answer"]
    assert "Running" not in copied


@pytest.mark.asyncio
async def test_in_place_resume_replaces_the_public_snapshot_and_keeps_the_session_usable(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Before resume"),
            backend_contract_mistral_response("After resume"),
        ]
    )
    _ = [event async for event in backend_contract_persistent_session.act("first")]
    session_id = backend_contract_persistent_session.session_id

    await backend_contract_persistent_session.resume(session_id)
    _ = [event async for event in backend_contract_persistent_session.act("second")]

    assert backend_contract_persistent_session.state.session.id == session_id
    assert [
        entry.text
        for entry in backend_contract_persistent_session.history
        if isinstance(entry, PublicMessageEntry)
    ] == ["first", "Before resume", "second", "After resume"]


@pytest.mark.asyncio
async def test_continue_attaches_the_latest_persisted_session_and_keeps_it_usable(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("saved"),
            backend_contract_mistral_response("continued"),
        ]
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        _ = [event async for event in session.act("save this")]
        session_id = session.session_id
    finally:
        await session.close()

    continued_connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    continued = await continued_connection.host.continue_session()
    try:
        _ = [event async for event in continued.act("continue this")]
        assert continued.session_id == session_id
        assert continued.state.session.id == session_id
        assert [
            entry.text
            for entry in continued.history
            if isinstance(entry, PublicMessageEntry)
        ] == ["save this", "saved", "continue this", "continued"]
    finally:
        await continued.close()


@pytest.mark.asyncio
async def test_resume_attaches_the_requested_persisted_session_and_keeps_it_usable(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_connection: BackendContractConnection,
    process_services: ProcessServices,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("saved"),
            backend_contract_mistral_response("resumed"),
        ]
    )
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        _ = [event async for event in session.act("save this")]
        session_id = session.session_id
    finally:
        await session.close()

    resumed_connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        _ = [event async for event in resumed.act("resume this")]
        assert resumed.session_id == session_id
        assert [
            entry.text
            for entry in resumed.history
            if isinstance(entry, PublicMessageEntry)
        ] == ["save this", "saved", "resume this", "resumed"]
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_auto_approve_starts_the_auto_approve_agent_and_stays_a_bypass_on_resume(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_config: None,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session saved on ``ask``.
    *Do*: Start a new session, then resume the saved one, both with auto-approve.
    *Assert*: The new one runs the auto-approve agent; the resumed one stays on
    ``ask`` with every call approved.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        side_effect=[backend_contract_mistral_response("saved")]
    )
    saving = await connect_backend_contract_host(
        session_options=SessionOptions(agent="ask"),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        saved = await saving.host.open_session()
        _ = [event async for event in saved.act("save this")]
        saved_id = saved.session_id
        await saved.close()
    finally:
        await saving.host.close()

    # Do
    async def open_with_auto_approve(session_id: str | None) -> tuple[str, bool]:
        connection = await connect_backend_contract_host(
            session_options=SessionOptions(auto_approve=True),
            capabilities=ClientCapabilities(),
            process_services=process_services,
        )
        try:
            session = await (
                connection.host.open_session()
                if session_id is None
                else connection.host.resume_session(session_id)
            )
            opened = (
                session.resources.agents.active.name,
                session.resources.runtime.bypass_tool_permissions,
            )
            await session.close()
            return opened
        finally:
            await connection.host.close()

    started_agent, started_bypass = await open_with_auto_approve(None)
    resumed_agent, resumed_bypass = await open_with_auto_approve(saved_id)

    # Assert
    assert (started_agent, started_bypass) == ("auto-approve", True)
    assert (resumed_agent, resumed_bypass) == ("ask", True)


@pytest.mark.asyncio
async def test_an_auto_approve_start_grants_its_folder_trust_once(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A trusted-for-the-session folder.
    *Do*: Start a session there with auto-approve, which builds its context twice.
    *Assert*: The folder holds a single session grant, released with the session.
    """
    from vibe.core.trusted_folders import trusted_folders_manager

    def grants() -> int:
        return trusted_folders_manager._session_trusted.count(str(tmp_path.resolve()))

    # Do
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path), trust_workspace=True, auto_approve=True
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        agent = session.resources.agents.active.name
        held = grants()
        await session.close()
    finally:
        await connection.host.close()

    # Assert
    assert agent == "auto-approve"
    assert held == 1


@pytest.mark.asyncio
async def test_stopping_a_session_leaves_its_siblings_on_a_shared_harness_serving(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=lambda _request: backend_contract_mistral_response("still here")
    )
    harness_host = LocalHarnessHost()
    options = LocalHarnessOptions(session_options=SessionOptions())
    try:
        stopped = await harness_host.start(options)
        sibling = await harness_host.start(options)

        await stopped.close()

        try:
            _ = [event async for event in sibling.act("are you there?")]
            sibling_messages = [
                entry.text
                for entry in sibling.history
                if isinstance(entry, PublicMessageEntry)
            ]
            later = await harness_host.start(options)
            await later.close()
        finally:
            await sibling.close()
    finally:
        await harness_host.close()

    assert sibling_messages == ["are you there?", "still here"]
