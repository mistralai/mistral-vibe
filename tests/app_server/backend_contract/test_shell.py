from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import BackendContractConnection
from vibe.app_server.events import HistoryEntryAdded, HistoryEntryUpdated
from vibe.app_server.models import CompletedEffectState, PublicEffectEntry
from vibe.app_server.protocol import AppServerResponseError, SessionShellCommandParams
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
