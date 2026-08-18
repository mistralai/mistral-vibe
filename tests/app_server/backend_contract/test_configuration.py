from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
import respx

from vibe.app_server.protocol import AppServerResponseError, ProtocolErrorCode
from vibe.app_server.session import AppServerSession


@pytest.mark.asyncio
async def test_session_configuration_updates_and_reloads_through_the_backend(
    backend_contract_session: AppServerSession,
) -> None:
    await backend_contract_session.resources.sessions.update_settings(
        max_turns=4, max_tokens=1024
    )
    await backend_contract_session.resources.config.update({"theme": "nord"})
    active_agent = await backend_contract_session.resources.agents.switch("plan")
    stripped_history_images = await backend_contract_session.resources.config.reload(
        reload_runtime=False
    )

    assert backend_contract_session.resources.config.current.theme == "nord"
    assert active_agent.name == "plan"
    assert stripped_history_images == 0


@pytest.mark.asyncio
async def test_config_updates_refresh_the_public_runtime_tool_catalog(
    backend_contract_session: AppServerSession,
) -> None:
    assert backend_contract_session.resources.runtime.has_tool("grep")

    await backend_contract_session.resources.config.update(
        {"disabled_tools": ["grep"]}, reload_runtime=True
    )

    assert not backend_contract_session.resources.runtime.has_tool("grep")


@pytest.mark.asyncio
async def test_config_subscribers_observe_updates_until_unsubscribed(
    backend_contract_session: AppServerSession,
) -> None:
    themes: list[str] = []
    unsubscribe = backend_contract_session.resources.config.subscribe(
        lambda config: themes.append(config.theme)
    )

    await backend_contract_session.resources.config.update({"theme": "monokai"})
    unsubscribe()
    await backend_contract_session.resources.config.update({"theme": "gruvbox"})

    assert themes == ["monokai"]


@pytest.mark.asyncio
async def test_runtime_mutations_leave_the_connection_usable_with_the_updated_runtime(
    backend_contract_session: AppServerSession,
) -> None:
    active_agent = await backend_contract_session.resources.agents.switch("plan")
    await backend_contract_session.resources.config.update(
        {"disabled_tools": ["grep"]}, reload_runtime=True
    )
    injected = await backend_contract_session.inject_user_context(
        "The runtime update is complete", as_message=True, client_message_id="runtime-1"
    )

    assert active_agent.name == "plan"
    assert backend_contract_session.resources.agents.active.name == "plan"
    assert not backend_contract_session.resources.runtime.has_tool("grep")
    assert injected[0].entry.id == "runtime-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write", "reload"])
async def test_config_mutations_conflict_while_a_turn_is_running(
    operation: str,
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "finished", started=started, release=release
        )
    )

    async def consume_turn() -> None:
        _ = [event async for event in backend_contract_session.act("wait")]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        with pytest.raises(AppServerResponseError) as exc_info:
            if operation == "write":
                await backend_contract_session.resources.config.update({
                    "theme": "nord"
                })
            else:
                await backend_contract_session.resources.config.reload()
    finally:
        release.set()
        await turn

    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
