from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
import json
from pathlib import Path
import shutil
from typing import Any

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from vibe.app_server._host_hooks import HOOK_MESSAGE_CODE
from vibe.app_server._plugins import PLUGIN_HOOKS_RELOAD_NOTICE
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
)
from vibe.app_server.events import ServerWarning
from vibe.app_server.protocol import ClientCapabilities, SessionOptions
from vibe.app_server.session import AppServerSession


@pytest.fixture
def runtime_notices(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """What the app server hands the Runtime to publish as a notice."""
    from mistralai_vibe_local_harness.vibe._session import UnifiedHarnessSessionBackend

    published: list[str] = []
    publish = UnifiedHarnessSessionBackend.publish_notice

    def recording(self: Any, message: str, **kwargs: Any) -> None:
        published.append(message)
        publish(self, message, **kwargs)

    monkeypatch.setattr(UnifiedHarnessSessionBackend, "publish_notice", recording)
    yield published


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_notice_held_for_the_client_reaches_it_as_a_warning_and_stays_out_of_the_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_persistent_connection: BackendContractConnection,
    tmp_path: Path,
    process_services: ProcessServices,
    runtime_notices: list[str],
) -> None:
    """*Prepare*: A persisted session whose saved scheduled loops are corrupt.
    *Do*: Resume it from a new Host.
    *Assert*: The app server's notice reaches the client as a ``warning``,
        without going through the Runtime, and the Session's history does not
        hold it.
    """
    # Prepare
    session = await backend_contract_persistent_connection.host.open_session()
    await session.resources.loops.create("30s", "kept")
    await session.inject_user_context("persist the session", as_message=True)
    session_id = session.session_id
    await session.close()
    store = tmp_path / "sessions" / "unified" / session_id / "scheduled-loops.json"
    store.write_text("{not json", encoding="utf-8")
    resumed_connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )

    # Do
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        warning = await asyncio.wait_for(
            _next_warning(resumed, "could not be restored"), timeout=5
        )
        history = resumed.history
    finally:
        await resumed.close()
        await resumed_connection.host.close()

    # Assert
    assert warning.params.warning.code == "warning"
    assert runtime_notices == []
    assert not [entry for entry in history if "could not be restored" in str(entry)]


@pytest.mark.both_paths
@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("true") is None, reason="needs a `true` command")
async def test_a_notice_after_the_client_attached_reaches_it_as_a_warning(
    backend_contract_mistral_api: respx.Route,
    tmp_path: Path,
    process_services: ProcessServices,
    runtime_notices: list[str],
) -> None:
    """*Prepare*: A session in a trusted folder with a plugin that has hooks.
    *Do*: Change the plugin's hooks, then reload plugins.
    *Assert*: The reload's notice reaches the attached client as a ``warning``,
        without going through the Runtime.
    """
    # Prepare
    plugin_root = tmp_path / ".vibe" / "plugins" / "productivity"
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.json").write_text(
        json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "productivity",
            "version": "1.0.0",
            "extensions": {"ai.mistral.vibe": {"schemaVersion": 1}},
        }),
        encoding="utf-8",
    )
    _write_plugin_hooks(plugin_root, "post_tool")
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(cwd=str(tmp_path), trust_workspace=True),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    session = await connection.host.open_session()
    try:
        _write_plugin_hooks(plugin_root, "pre_tool")

        # Do
        await session.resources.plugins.reload()
        warning = await asyncio.wait_for(
            _next_warning(session, PLUGIN_HOOKS_RELOAD_NOTICE), timeout=5
        )
    finally:
        await session.close()
        await connection.host.close()

    # Assert
    assert warning.params.warning.code == "warning"
    assert runtime_notices == []


@pytest.mark.both_paths
@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("printf") is None, reason="needs a `printf` command")
async def test_session_start_hook_notices_reach_the_client(
    backend_contract_mistral_api: respx.Route,
    tmp_path: Path,
    process_services: ProcessServices,
    runtime_notices: list[str],
) -> None:
    """*Prepare*: A trusted folder with a session_start hook that shows a
        message and one whose output is not JSON.
    *Do*: Start a session.
    *Assert*: The hook's message and the failure reach the client as
        ``warning``, without going through the Runtime.
    """
    # Prepare
    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "hooks.toml").write_text(
        "[[hooks]]\n"
        'name = "load"\n'
        'type = "session_start"\n'
        """command = "printf '{\\"system_message\\": \\"loaded\\"}'"\n"""
        "[[hooks]]\n"
        'name = "broken"\n'
        'type = "session_start"\n'
        """command = "printf 'not json'"\n""",
        encoding="utf-8",
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(cwd=str(tmp_path), trust_workspace=True),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )

    # Do
    session = await connection.host.open_session()
    try:
        # Failures go out before the messages.
        failure = await asyncio.wait_for(
            _next_warning(session, "session_start hook 'broken' failed"), timeout=5
        )
        message = await asyncio.wait_for(_next_warning(session, "loaded"), timeout=5)
    finally:
        await session.close()
        await connection.host.close()

    # Assert
    assert message.params.warning.code == HOOK_MESSAGE_CODE
    assert failure.params.warning.code == "warning"
    assert runtime_notices == []


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_notice_while_compaction_holds_its_events_reaches_the_client(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_persistent_session: AppServerSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A session with one turn, where a notice is raised once the
        compaction is done and before it answers, as background work can.
    *Do*: Compact.
    *Assert*: The compaction answers and the notice reaches the client.
    """
    # Prepare
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response("Before compaction"),
            backend_contract_mistral_response("<summary>Summary</summary>"),
        ]
    )
    session = backend_contract_persistent_session
    _ = [event async for event in session.act("first question")]
    compact = UnifiedHarnessBackendAdapter.compact

    async def compact_then_notice(self: Any, params: Any) -> Any:
        result = await compact(self, params)
        # Lets the stream reach the compaction's updates, held until the reply.
        await asyncio.sleep(0.5)
        self._publish_notice("raised while compacting")
        return result

    monkeypatch.setattr(UnifiedHarnessBackendAdapter, "compact", compact_then_notice)

    # Do
    summary = await asyncio.wait_for(session.compact(), timeout=10)
    warning = await asyncio.wait_for(
        _next_warning(session, "raised while compacting"), timeout=5
    )

    # Assert
    assert summary == "Summary"
    assert warning.params.warning.code == "warning"


def _write_plugin_hooks(plugin_root: Path, point: str) -> None:
    (plugin_root / "ai.mistral.vibe").mkdir(exist_ok=True)
    (plugin_root / "ai.mistral.vibe" / "hooks.toml").write_text(
        f'[[hooks]]\nname = "audit"\ntype = "{point}"\ncommand = "true"\n',
        encoding="utf-8",
    )


async def _next_warning(session: AppServerSession, text: str) -> ServerWarning:
    async for event in session.events():
        if isinstance(event, ServerWarning) and text in event.params.warning.message:
            return event
    raise AssertionError(f"no warning saying {text!r}")
