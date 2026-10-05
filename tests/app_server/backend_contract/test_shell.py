from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server.events import HistoryEntryAdded, HistoryEntryUpdated
from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    FailedEffectState,
    PublicEffectEntry,
    RunningEffectState,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    SessionOptions,
    SessionShellCommandParams,
)
from vibe.app_server.session import AppServerSession
from vibe.utils.tool_presentation import ToolEffectKind


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_streams_one_effect_entry_to_the_timeline(
    backend_contract_session: AppServerSession,
) -> None:
    events = [
        event
        async for event in backend_contract_session.resources.shell.run(
            "printf 'hello from the shell'"
        )
    ]

    added = next(event for event in events if isinstance(event, HistoryEntryAdded))
    assert isinstance(added.entry, PublicEffectEntry), (
        "the terminal has no shell effect to draw"
    )
    assert added.entry.detail.kind is ToolEffectKind.SHELL
    updates = [event for event in events if isinstance(event, HistoryEntryUpdated)]
    assert updates, "the effect never reported an outcome"
    assert all(update.entry.id == added.entry.id for update in updates)

    final = updates[-1].entry
    assert isinstance(final, PublicEffectEntry)
    assert isinstance(final.state, CompletedEffectState)
    assert final.state.output_text == "hello from the shell"
    assert any(entry.id == final.id for entry in backend_contract_session.history)


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_injects_its_output_as_model_context(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[backend_contract_mistral_response("answer")]
    )

    _ = [
        event
        async for event in backend_contract_session.resources.shell.run(
            "printf 'hello from the shell'"
        )
    ]
    _ = [event async for event in backend_contract_session.act("what did that print?")]

    sent = backend_contract_mistral_api.calls.last.request.content.decode()
    assert "Manual `!` command result" in sent, (
        "the shell result never reached the model as context"
    )
    assert "hello from the shell" in sent


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_reports_the_exit_code_of_a_failure(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    backend_contract_mistral_api.mock(
        side_effect=[backend_contract_mistral_response("answer")]
    )

    _ = [
        event
        async for event in backend_contract_session.resources.shell.run(
            "printf 'nope' >&2; exit 3"
        )
    ]
    _ = [event async for event in backend_contract_session.act("what happened?")]

    sent = backend_contract_mistral_api.calls.last.request.content.decode()
    assert "Exit code: 3" in sent
    assert "nope" in sent


@pytest.mark.asyncio
async def test_manual_shell_command_is_confined_to_the_workspace(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    # The filesystem root can never be inside the session's workspace.
    outside = Path(Path.cwd().anchor)

    with pytest.raises(AppServerResponseError, match="outside the workspace"):
        _ = await backend_contract_connection.client.request(
            "session/shellCommand",
            SessionShellCommandParams(
                session_id=backend_contract_session.session_id,
                command="pwd",
                action="run",
                cwd=str(outside),
            ),
        )


def _final_shell_entry(
    events: list[HistoryEntryAdded | HistoryEntryUpdated],
) -> PublicEffectEntry:
    updates = [
        event.entry
        for event in events
        if isinstance(event, HistoryEntryUpdated)
        and isinstance(event.entry, PublicEffectEntry)
    ]
    assert updates, "the effect never reported an outcome"
    return updates[-1]


def _shell_history(session: AppServerSession) -> list[PublicEffectEntry]:
    return [
        entry
        for entry in session.history
        if isinstance(entry, PublicEffectEntry)
        and entry.detail.kind is ToolEffectKind.SHELL
    ]


async def _resume_on_a_new_host(
    session_id: str, experimental_harness: bool
) -> tuple[AppServerSession, BackendContractConnection]:
    """Resume a stored session through a brand-new Host, as a restart would."""
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    session = await connection.host.resume_session(session_id)
    return session, connection


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_survives_a_new_host(
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    """*Prepare*: A persistent session that ran one manual shell command.
    *Do*: Close it, resume it through a brand-new Host, and read the history.
    *Assert*: The shell effect restores once, with the same id and terminal state.
    """
    session = await backend_contract_persistent_connection.host.open_session()
    events = [
        event
        async for event in session.resources.shell.run("printf 'hello from the shell'")
    ]
    final = _final_shell_entry(events)
    assert isinstance(final.state, CompletedEffectState)
    session_id = session.session_id
    await session.close()

    resumed, resumed_connection = await _resume_on_a_new_host(
        session_id, experimental_harness
    )
    try:
        restored = _shell_history(resumed)
        assert len(restored) == 1, (
            "the shell effect duplicated or vanished across the restart"
        )
        assert restored[0].id == final.id
        assert restored[0].title == final.title
        assert restored[0].detail == final.detail
        assert isinstance(restored[0].state, CompletedEffectState)
        assert restored[0].state.output_text == final.state.output_text
        assert restored[0].state.duration_ms == final.state.duration_ms
    finally:
        await resumed.close()
        await resumed_connection.host.close()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_timeout_state_survives_a_new_host(
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    """*Prepare*: A persistent session whose manual shell command timed out.
    *Do*: Close it and resume it through a brand-new Host.
    *Assert*: The restored effect reports the timed-out failure, not a phantom run.
    """
    session = await backend_contract_persistent_connection.host.open_session()
    events = [
        event
        async for event in session.resources.shell.run(
            "printf 'slow'; sleep 5", timeout_seconds=0.2
        )
    ]
    final = _final_shell_entry(events)
    assert isinstance(final.state, FailedEffectState)
    assert final.state.error.message == "Command timed out"
    session_id = session.session_id
    await session.close()

    resumed, resumed_connection = await _resume_on_a_new_host(
        session_id, experimental_harness
    )
    try:
        restored = _shell_history(resumed)
        assert len(restored) == 1
        assert restored[0].id == final.id
        assert isinstance(restored[0].state, FailedEffectState)
        assert restored[0].state.error.message == "Command timed out"
        assert restored[0].state.output_text == "slow"
    finally:
        await resumed.close()
        await resumed_connection.host.close()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_interrupt_state_survives_a_new_host(
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    """*Prepare*: A persistent session whose manual shell command was
    interrupted by the user mid-run.
    *Do*: Close it and resume it through a brand-new Host.
    *Assert*: The restored effect reports the interruption, not a phantom run.
    """
    session = await backend_contract_persistent_connection.host.open_session()
    stream = session.resources.shell.run("printf 'slow'; sleep 30")
    added = await anext(stream)
    assert isinstance(added, HistoryEntryAdded), "the terminal has no live effect"
    operation_id = added.entry.id
    events: list[HistoryEntryAdded | HistoryEntryUpdated] = [added]
    # Interrupt only once the streamed output landed, so the recorded state is
    # deterministic instead of racing the command's first write.
    async for event in stream:
        events.append(event)
        if (
            isinstance(event, HistoryEntryUpdated)
            and isinstance(event.entry, PublicEffectEntry)
            and isinstance(event.entry.state, RunningEffectState)
            and event.entry.state.output_text
        ):
            break
    await backend_contract_persistent_connection.client.request(
        "session/shellCommand",
        SessionShellCommandParams(
            session_id=session.session_id, operation_id=operation_id, action="interrupt"
        ),
    )
    events.extend([event async for event in stream])
    final = _final_shell_entry(events)
    assert isinstance(final.state, CancelledEffectState)
    assert final.state.reason == "Command interrupted"
    session_id = session.session_id
    await session.close()

    resumed, resumed_connection = await _resume_on_a_new_host(
        session_id, experimental_harness
    )
    try:
        restored = _shell_history(resumed)
        assert len(restored) == 1
        assert restored[0].id == operation_id
        assert isinstance(restored[0].state, CancelledEffectState)
        assert restored[0].state.reason == "Command interrupted"
        assert restored[0].state.output_text == "slow"
    finally:
        await resumed.close()
        await resumed_connection.host.close()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_output_is_capped_in_the_stored_record(
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    """*Prepare*: A persistent Unified session whose command printed past the
    model limit.
    *Do*: Close it and resume it through a brand-new Host.
    *Assert*: The stored record holds no more output than the model saw, while
    the live entry showed everything. The legacy log deliberately stores the
    full output, so this bound is Unified-specific.
    """
    if not experimental_harness:
        pytest.skip("the Unified record caps output; the legacy log stores it in full")
    limit = 16_000
    marker = "\n... [truncated]"

    session = await backend_contract_persistent_connection.host.open_session()
    events = [
        event async for event in session.resources.shell.run("yes x | head -c 20000")
    ]
    final = _final_shell_entry(events)
    assert isinstance(final.state, CompletedEffectState)
    full_output = final.state.output_text
    assert len(full_output) == 20_000, "the live stream must stay uncapped"
    session_id = session.session_id
    await session.close()

    resumed, resumed_connection = await _resume_on_a_new_host(
        session_id, experimental_harness
    )
    try:
        restored = _shell_history(resumed)
        assert len(restored) == 1
        assert isinstance(restored[0].state, CompletedEffectState)
        assert restored[0].state.output_text == full_output[:limit] + marker
        output = restored[0].state.output
        assert isinstance(output, dict)
        assert output["truncated"] is True
    finally:
        await resumed.close()
        await resumed_connection.host.close()


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_manual_shell_command_rejects_a_reused_operation_id_after_restart(
    backend_contract_persistent_connection: BackendContractConnection,
    experimental_harness: bool,
) -> None:
    """*Prepare*: A persistent session holding one recorded shell effect.
    *Do*: Resume through a brand-new Host and rerun the same operation id.
    *Assert*: The rerun is rejected and the history still holds one entry.
    """
    session = await backend_contract_persistent_connection.host.open_session()
    _ = await backend_contract_persistent_connection.client.request(
        "session/shellCommand",
        SessionShellCommandParams(
            session_id=session.session_id,
            command="printf 'first run'",
            action="run",
            operation_id="shell-op-1",
        ),
    )
    session_id = session.session_id
    await session.close()

    resumed, resumed_connection = await _resume_on_a_new_host(
        session_id, experimental_harness
    )
    try:
        with pytest.raises(AppServerResponseError, match="shell-op-1"):
            _ = await resumed_connection.client.request(
                "session/shellCommand",
                SessionShellCommandParams(
                    session_id=resumed.session_id,
                    command="printf 'second run'",
                    action="run",
                    operation_id="shell-op-1",
                ),
            )
        assert [entry.id for entry in _shell_history(resumed)] == ["shell-op-1"]
    finally:
        await resumed.close()
        await resumed_connection.host.close()
