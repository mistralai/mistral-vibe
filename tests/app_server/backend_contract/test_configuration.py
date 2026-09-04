from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path
import tomllib

import httpx
import pytest
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ProtocolErrorCode,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession
from vibe.core.config import VibeConfigSchema


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
async def test_active_model_is_pinned_on_first_turn_and_restored_on_resume(
    config_dir: Path,
    tmp_path: Path,
    experimental_harness: bool,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = ""
    config["models"].append({
        "name": "alternate-model",
        "provider": "mistral",
        "alias": "alternate",
    })
    session_root = tmp_path / "sessions"
    config["session_logging"] = {"enabled": True, "save_dir": str(session_root)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )

    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    session = await connection.host.open_session()
    try:
        assert session.resources.config.current.active_model_pinned is False
        default_model = session.resources.config.current.active_model.alias
        await session.resources.config.update(
            {"active_model": "alternate"}, reload_runtime=True
        )
        assert session.resources.config.current.active_model.alias == "alternate"
        assert (
            _session_model_is_persisted(
                session_root, session.session_id, experimental_harness
            )
            is False
        )
        await session.resources.config.update({"active_model": ""}, reload_runtime=True)
        _ = [event async for event in session.act("pin this model")]
        session_id = session.session_id
        assert session.resources.config.current.active_model_pinned is True

        await session.resources.config.update(
            {"active_model": "alternate"}, reload_runtime=True
        )
        assert session.resources.config.current.active_model.alias == "alternate"
        assert _stored_active_model(session_root, session_id, experimental_harness) == (
            default_model
        )
        _ = [event async for event in session.act("persist the alternate model")]
        assert _stored_active_model(session_root, session_id, experimental_harness) == (
            "alternate"
        )

        await session.resources.config.update({"active_model": ""}, reload_runtime=True)
        assert session.resources.config.current.active_model.alias == default_model
        assert (
            _stored_active_model(session_root, session_id, experimental_harness)
            == "alternate"
        )
        _ = [event async for event in session.act("persist the default model")]
        assert (
            _stored_active_model(session_root, session_id, experimental_harness)
            == default_model
        )
    finally:
        await session.close()

    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert config["active_model"] == ""
    config["active_model"] = "alternate"
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    resumed_connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        assert resumed.resources.config.current.active_model.alias == default_model
        assert resumed.resources.config.current.active_model_pinned is True
        await resumed.clear_history()
        assert resumed.session_id != session_id
        assert resumed.resources.config.current.active_model.alias == "alternate"
    finally:
        await resumed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_layer", "updates_session_pin"), [("user-toml", False), ("overrides", True)]
)
async def test_explicit_active_model_target_controls_session_pin_update(
    config_dir: Path,
    tmp_path: Path,
    experimental_harness: bool,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    target_layer: str,
    updates_session_pin: bool,
) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = ""
    config["models"].append({
        "name": "alternate-model",
        "provider": "mistral",
        "alias": "alternate",
    })
    session_root = tmp_path / "sessions"
    config["session_logging"] = {"enabled": True, "save_dir": str(session_root)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )

    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    session = await connection.host.open_session()
    try:
        _ = [event async for event in session.act("pin this model")]
        pinned_model = session.resources.config.current.active_model.alias
        assert (
            _stored_active_model(session_root, session.session_id, experimental_harness)
            == pinned_model
        )

        await session.resources.config.update(
            {"active_model": "alternate"},
            target_layer=target_layer,
            reload_runtime=True,
        )

        active_model = "alternate" if updates_session_pin else pinned_model
        assert session.resources.config.current.active_model.alias == active_model
        assert (
            _stored_active_model(session_root, session.session_id, experimental_harness)
            == pinned_model
        )

        _ = [event async for event in session.act("persist the selected model")]
        assert (
            _stored_active_model(session_root, session.session_id, experimental_harness)
            == active_model
        )
    finally:
        await session.close()

    persisted_config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert persisted_config["active_model"] == (
        "" if updates_session_pin else "alternate"
    )


@pytest.mark.asyncio
async def test_removed_session_active_model_falls_back_to_default(
    config_dir: Path,
    tmp_path: Path,
    experimental_harness: bool,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    fallback_model = (
        VibeConfigSchema(active_model="removed-model").get_active_model().alias
    )
    config["active_model"] = "removed-model"
    config["models"].extend([
        {"name": "removed-model", "provider": "mistral", "alias": "removed-model"},
        {
            "name": "lower-layer-model",
            "provider": "mistral",
            "alias": "lower-layer-model",
        },
    ])
    session_root = tmp_path / "sessions"
    config["session_logging"] = {"enabled": True, "save_dir": str(session_root)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("stored")
    )

    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    session = await connection.host.open_session()
    try:
        _ = [event async for event in session.act("pin the removed model")]
        session_id = session.session_id
        assert (
            _stored_active_model(session_root, session_id, experimental_harness)
            == "removed-model"
        )
    finally:
        await session.close()

    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = "lower-layer-model"
    config["models"] = [
        model for model in config["models"] if model["alias"] != "removed-model"
    ]
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    resumed_connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        assert resumed.resources.config.current.active_model.alias == fallback_model
        assert (
            resumed.resources.config.current.active_model.alias != "lower-layer-model"
        )
        assert resumed.resources.config.current.active_model_pinned is False
        assert (
            _stored_active_model(session_root, session_id, experimental_harness)
            == "removed-model"
        )
        _ = [event async for event in resumed.act("pin the default model")]
        assert resumed.resources.config.current.active_model_pinned is True
        assert (
            _stored_active_model(session_root, session_id, experimental_harness)
            == fallback_model
        )
    finally:
        await resumed.close()


def _stored_active_model(
    session_root: Path, session_id: str, experimental_harness: bool
) -> str | None:
    if experimental_harness:
        from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
            UnifiedSessionStore,
        )

        return (
            UnifiedSessionStore(session_root, session_id)
            .load()
            .runtime_state.session_metadata.active_model
        )
    metadata_path = next(session_root.glob(f"*_{session_id[:8]}/meta.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return metadata["config"].get("active_model")


def _session_model_is_persisted(
    session_root: Path, session_id: str, experimental_harness: bool
) -> bool:
    if experimental_harness:
        from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
            UnifiedSessionStore,
        )

        return UnifiedSessionStore(session_root, session_id).exists
    return any(session_root.glob(f"*_{session_id[:8]}/meta.json"))


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
