from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable
import contextlib
import json
from pathlib import Path
import tomllib

import httpx
import pytest
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server.events import ServerWarning, TurnCompleted
from vibe.app_server.models import FileImageSource, ImageAttachment
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ConfigWriteOpWire,
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
    active_agent = await backend_contract_session.resources.agents.switch(
        "auto-approve"
    )
    stripped_history_images = await backend_contract_session.resources.config.reload(
        reload_runtime=False
    )

    assert backend_contract_session.resources.config.current.theme == "nord"
    assert active_agent.name == "auto-approve"
    assert stripped_history_images == 0


@pytest.mark.asyncio
async def test_a_model_pick_goes_through_the_typed_write(
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: A session on its configured model.
    *Do*: Pick the thinking level, the way `/thinking` does, from those offered.
    *Assert*: It lands. The CLI used to post a JSON pointer it escaped itself at
    `config/write`, which a running turn refuses outright; the typed operation
    is the one the app-server can park until the turn ends.
    """
    config = backend_contract_session.resources.config
    assert config.current.active_model.thinking == "off"
    assert "high" in config.current.active_model.thinking_levels

    await config.write_model(reasoning_effort="high")

    assert config.current.active_model.thinking == "high"


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
async def test_active_model_update_preserves_usage_and_updates_prices(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: Configure prices and accumulate usage with the default model.
    *Do*: Switch to the differently priced built-in medium model.
    *Assert*: Usage totals remain while prices change to the new model's rates.
    """
    # Prepare
    await backend_contract_session.resources.config.update(
        {
            "models": [
                {
                    "name": "mistral-vibe-cli-latest",
                    "provider": "mistral",
                    "alias": "devstral-latest",
                    "input_price": 1.0,
                    "output_price": 2.0,
                }
            ]
        },
        reload_runtime=True,
    )
    backend_contract_mistral_api.mock(
        return_value=backend_contract_mistral_response("priced")
    )
    _ = [event async for event in backend_contract_session.act("count this turn")]
    before = backend_contract_session.resources.runtime.stats
    assert before.input_price_per_million == 1.0
    assert before.output_price_per_million == 2.0

    # Do
    await backend_contract_session.resources.config.update(
        {"active_model": "mistral-medium-3.5"}, reload_runtime=True
    )

    # Assert
    after = backend_contract_session.resources.runtime.stats
    assert after.session_prompt_tokens == before.session_prompt_tokens
    assert after.session_completion_tokens == before.session_completion_tokens
    assert after.input_price_per_million == 1.5
    assert after.output_price_per_million == 7.5


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
        session_options=SessionOptions(), capabilities=ClientCapabilities()
    )
    session = await connection.host.open_session()
    try:
        assert session.resources.config.current.active_model_pinned is False
        default_model = session.resources.config.current.active_model.alias
        await session.resources.config.update(
            {"active_model": "alternate"}, reload_runtime=True
        )
        assert session.resources.config.current.active_model.alias == "alternate"
        assert _session_model_is_persisted(session_root, session.session_id) is False
        await session.resources.config.update({"active_model": ""}, reload_runtime=True)
        _ = [event async for event in session.act("pin this model")]
        session_id = session.session_id
        assert session.resources.config.current.active_model_pinned is True

        await session.resources.config.update(
            {"active_model": "alternate"}, reload_runtime=True
        )
        assert session.resources.config.current.active_model.alias == "alternate"
        assert _stored_active_model(session_root, session_id) == (default_model)
        _ = [event async for event in session.act("persist the alternate model")]
        assert _stored_active_model(session_root, session_id) == ("alternate")

        await session.resources.config.update({"active_model": ""}, reload_runtime=True)
        assert session.resources.config.current.active_model.alias == default_model
        assert _stored_active_model(session_root, session_id) == "alternate"
        _ = [event async for event in session.act("persist the default model")]
        assert _stored_active_model(session_root, session_id) == default_model
    finally:
        await session.close()

    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert config["active_model"] == ""
    config["active_model"] = "alternate"
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    resumed_connection = await connect_backend_contract_host(
        session_options=SessionOptions(), capabilities=ClientCapabilities()
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
        session_options=SessionOptions(), capabilities=ClientCapabilities()
    )
    session = await connection.host.open_session()
    try:
        _ = [event async for event in session.act("pin this model")]
        pinned_model = session.resources.config.current.active_model.alias
        assert _stored_active_model(session_root, session.session_id) == pinned_model

        await session.resources.config.update(
            {"active_model": "alternate"},
            target_layer=target_layer,
            reload_runtime=True,
        )

        active_model = "alternate" if updates_session_pin else pinned_model
        assert session.resources.config.current.active_model.alias == active_model
        assert _stored_active_model(session_root, session.session_id) == pinned_model

        _ = [event async for event in session.act("persist the selected model")]
        assert _stored_active_model(session_root, session.session_id) == active_model
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
        session_options=SessionOptions(), capabilities=ClientCapabilities()
    )
    session = await connection.host.open_session()
    try:
        _ = [event async for event in session.act("pin the removed model")]
        session_id = session.session_id
        assert _stored_active_model(session_root, session_id) == "removed-model"
    finally:
        await session.close()

    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = "lower-layer-model"
    config["models"] = [
        model for model in config["models"] if model["alias"] != "removed-model"
    ]
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    resumed_connection = await connect_backend_contract_host(
        session_options=SessionOptions(), capabilities=ClientCapabilities()
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        assert resumed.resources.config.current.active_model.alias == fallback_model
        assert (
            resumed.resources.config.current.active_model.alias != "lower-layer-model"
        )
        assert resumed.resources.config.current.active_model_pinned is False
        assert _stored_active_model(session_root, session_id) == "removed-model"
        _ = [event async for event in resumed.act("pin the default model")]
        assert resumed.resources.config.current.active_model_pinned is True
        assert _stored_active_model(session_root, session_id) == fallback_model
    finally:
        await resumed.close()


def _stored_active_model(session_root: Path, session_id: str) -> str | None:
    from mistralai_vibe_local_harness.vibe._storage import UnifiedSessionStore

    return (
        UnifiedSessionStore(session_root, session_id)
        .load()
        .runtime_state.session_metadata.active_model
    )


def _session_model_is_persisted(session_root: Path, session_id: str) -> bool:
    from mistralai_vibe_local_harness.vibe._storage import UnifiedSessionStore

    return UnifiedSessionStore(session_root, session_id).exists


@pytest.mark.asyncio
async def test_runtime_mutations_leave_the_connection_usable_with_the_updated_runtime(
    backend_contract_session: AppServerSession,
) -> None:
    active_agent = await backend_contract_session.resources.agents.switch(
        "auto-approve"
    )
    await backend_contract_session.resources.config.update(
        {"disabled_tools": ["grep"]}, reload_runtime=True
    )
    injected = await backend_contract_session.inject_user_context(
        "The runtime update is complete", as_message=True, client_message_id="runtime-1"
    )

    assert active_agent.name == "auto-approve"
    assert backend_contract_session.resources.agents.active.name == "auto-approve"
    assert not backend_contract_session.resources.runtime.has_tool("grep")
    assert injected[0].entry.id == "runtime-1"


@contextlib.asynccontextmanager
async def _turn_running(
    session: AppServerSession,
    gated_response: Callable[..., httpx.Response],
    mistral_api: respx.Route,
) -> AsyncIterator[None]:
    started = asyncio.Event()
    release = asyncio.Event()
    mistral_api.mock(
        return_value=gated_response("finished", started=started, release=release)
    )

    async def consume_turn() -> None:
        _ = [event async for event in session.act("wait")]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        yield
    finally:
        release.set()
        await turn


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["write", "mixed_write", "reload", "teleport"])
async def test_turn_affecting_mutations_conflict_while_a_turn_is_running(
    operation: str,
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    resources = backend_contract_session.resources
    async with _turn_running(
        backend_contract_session,
        backend_contract_gated_mistral_response,
        backend_contract_mistral_api,
    ):
        with pytest.raises(AppServerResponseError) as exc_info:
            match operation:
                case "write":
                    await resources.config.update({"disabled_tools": ["grep"]})
                case "mixed_write":
                    await resources.config.update({
                        "theme": "nord",
                        "disabled_tools": ["grep"],
                    })
                case "reload":
                    await resources.config.reload()
                case _:
                    await resources.vibe_code.open_projects(for_teleport=True)

    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("theme", "nord"),
        ("voice_mode_enabled", True),
        ("narrator_enabled", True),
        ("log_level", "DEBUG"),
    ],
)
async def test_display_settings_save_while_a_turn_is_running(
    key: str,
    value: str | bool,
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    async with _turn_running(
        backend_contract_session,
        backend_contract_gated_mistral_response,
        backend_contract_mistral_api,
    ):
        response = await backend_contract_session.resources.config.write(
            [ConfigWriteOpWire(op="set", path=f"/{key}", value=value)],
            reason="display setting",
        )

    assert not response.rejected
    assert not response.failures
    assert getattr(response.runtime.config, key) == value


@pytest.mark.asyncio
async def test_proxy_settings_save_while_a_turn_is_running(
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    config = backend_contract_session.resources.config
    async with _turn_running(
        backend_contract_session,
        backend_contract_gated_mistral_response,
        backend_contract_mistral_api,
    ):
        await config.update_proxy({"HTTP_PROXY": "http://proxy.example.com:8080"})

    settings = await config.read_proxy()
    assert settings.values["HTTP_PROXY"] == "http://proxy.example.com:8080"


@pytest.mark.asyncio
async def test_remote_project_picker_reaches_the_repository_check_while_a_turn_is_running(
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
    backend_contract_mistral_api: respx.Route,
    backend_contract_session: AppServerSession,
) -> None:
    async with _turn_running(
        backend_contract_session,
        backend_contract_gated_mistral_response,
        backend_contract_mistral_api,
    ):
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_session.resources.vibe_code.open_projects()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "requires a git repository" in exc_info.value.error.message


@pytest.mark.asyncio
async def test_agent_install_and_uninstall_round_trip_through_the_backend(
    backend_contract_session: AppServerSession,
) -> None:
    def agent_names() -> set[str]:
        return {agent.name for agent in backend_contract_session.resources.agents.all}

    assert "lean" not in agent_names()

    await backend_contract_session.resources.agents.set_installed(
        "lean", installed=True
    )
    await backend_contract_session.resources.runtime.refresh()

    assert "lean" in agent_names()

    await backend_contract_session.resources.agents.set_installed(
        "lean", installed=False
    )
    await backend_contract_session.resources.runtime.refresh()

    assert "lean" not in agent_names()


@pytest.mark.asyncio
async def test_agent_install_rejects_unknown_agent_names(
    backend_contract_session: AppServerSession,
) -> None:

    with pytest.raises(AppServerResponseError) as exc_info:
        await backend_contract_session.resources.agents.set_installed(
            "no-such-agent", installed=True
        )

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "no-such-agent" in exc_info.value.error.message


@pytest.mark.asyncio
async def test_agent_uninstall_switches_away_from_the_active_agent(
    backend_contract_session: AppServerSession,
) -> None:

    resources = backend_contract_session.resources
    await resources.agents.set_installed("lean", installed=True)
    active = await resources.agents.switch("lean")
    assert active.name == "lean"

    await resources.agents.set_installed("lean", installed=False)

    assert "lean" not in {agent.name for agent in resources.agents.all}
    assert resources.agents.active.name != "lean"


# A valid 1x1 PNG: the Harness reads the file's dimensions to cost the image.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
@pytest.mark.parametrize("pick", ["model_write", "config_write", "reload"])
async def test_a_refused_model_pick_leaves_the_session_and_config_on_the_old_model(
    config_dir: Path,
    tmp_path: Path,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    pick: str,
) -> None:
    """*Prepare*: A session whose newest message holds an image and more text
    than a small model's compaction threshold. A message with an image cannot
    be truncated, so the context cannot move to that model.
    *Do*: Pick the small model, the way `/model` does (or through a raw
    `config/write`, or by shrinking its model in config.toml and running
    `/reload`), then queue a turn.
    *Assert*: The pick is refused as invalid params with Core's reason. The
    queued turn runs on the old model, and config.toml still names it. The pick used to be written to config.toml
    before Core refused it, then parked and retried at every turn start, so no
    queued turn ever started.
    """
    # Prepare
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["models"] = [
        {
            "name": "roomy-model",
            "provider": "mistral",
            "alias": "roomy",
            "supports_images": True,
        },
        {
            "name": "cramped-model",
            "provider": "mistral",
            "alias": "cramped",
            "supports_images": True,
            "max_context_length": 16_384,
        },
    ]
    config["active_model"] = "roomy"
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    models: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return backend_contract_mistral_response("noted")

    backend_contract_mistral_api.mock(side_effect=respond)
    image = tmp_path / "shot.png"
    image.write_bytes(_PNG)
    attachment = ImageAttachment(
        source=FileImageSource(path=str(image)), alias="shot.png", mime_type="image/png"
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(cwd=str(tmp_path)),
        capabilities=ClientCapabilities(),
    )
    session = await connection.host.open_session()
    try:
        _ = [
            event async for event in session.act("look " * 30_000, images=[attachment])
        ]
        models_before = len(models)

        # Do
        with pytest.raises(AppServerResponseError) as refused:
            if pick == "model_write":
                await session.resources.config.write_model(model_alias="cramped")
            elif pick == "config_write":
                await session.resources.config.update(
                    {"active_model": "cramped"}, reload_runtime=True
                )
            else:
                # The session is pinned to its model by now, so a hand edit
                # reaches it by shrinking that model rather than naming another.
                edited = tomllib.loads(config_file.read_text(encoding="utf-8"))
                edited["models"][0]["max_context_length"] = 16_384
                config_file.write_text(tomli_w.dumps(edited), encoding="utf-8")
                await session.resources.config.reload()
        await session.enqueue("still here?")
        await asyncio.wait_for(_next_turn_completed(session), timeout=5)
    finally:
        await session.close()

    # Assert
    assert refused.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "cannot move to a model" in refused.value.error.message
    assert len(models) > models_before
    assert set(models) == {"roomy-model"}
    persisted = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["active_model"] == "roomy"


@pytest.mark.asyncio
async def test_a_mid_turn_pick_core_refuses_at_the_boundary_is_shown_and_the_session_carries_on(
    config_dir: Path,
    tmp_path: Path,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[[str], httpx.Response],
    backend_contract_gated_mistral_response: Callable[..., httpx.Response],
) -> None:
    """*Prepare*: A session on a roomy model, mid-turn, whose context still
    fits a small model.
    *Do*: Pick the small model the way `/model` does, then let the turn add more
    than the small model can take, so Core refuses the pick at the boundary.
    Queue a turn.
    *Assert*: The refusal reaches the client as a warning with Core's reason,
    the session reports the roomy model it still runs, and the queued turn runs
    on it. A parked refusal used to fail every turn start after it, so no
    queued turn ever ran.
    """
    # Prepare
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["models"] = [
        {
            "name": "roomy-model",
            "provider": "mistral",
            "alias": "roomy",
            "supports_images": True,
        },
        {
            "name": "cramped-model",
            "provider": "mistral",
            "alias": "cramped",
            "supports_images": True,
            "max_context_length": 65_536,
        },
    ]
    config["active_model"] = "roomy"
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    started = asyncio.Event()
    release = asyncio.Event()
    models: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        if len(models) == 1:
            return backend_contract_gated_mistral_response(
                "noted", started=started, release=release
            )
        return backend_contract_mistral_response("noted")

    backend_contract_mistral_api.mock(side_effect=respond)
    image = tmp_path / "shot.png"
    image.write_bytes(_PNG)
    attachment = ImageAttachment(
        source=FileImageSource(path=str(image)), alias="shot.png", mime_type="image/png"
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(cwd=str(tmp_path)),
        capabilities=ClientCapabilities(),
    )
    session = await connection.host.open_session()
    try:

        async def consume_turn() -> None:
            _ = [event async for event in session.act("look")]

        turn = asyncio.create_task(consume_turn())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)

            # Do
            await session.resources.config.write_model(model_alias="cramped")
            await session.inject_user_context(
                "look " * 120_000, images=[attachment], require_active_turn=True
            )
        finally:
            release.set()
            await turn
        await asyncio.wait_for(
            _next_warning(session, "cannot move to a model"), timeout=5
        )
        # The runtime is announced on its own channel, so it can trail.
        await asyncio.wait_for(_reported_model(session, "roomy"), timeout=5)
        await session.enqueue("still here?")
        await asyncio.wait_for(_next_turn_completed(session), timeout=5)
    finally:
        await session.close()

    # Assert
    assert len(models) == 3
    assert set(models) == {"roomy-model"}


async def _next_warning(session: AppServerSession, text: str) -> None:
    async for event in session.events():
        if isinstance(event, ServerWarning) and text in event.params.warning.message:
            return


async def _reported_model(session: AppServerSession, alias: str) -> None:
    while session.resources.config.current.active_model.alias != alias:
        await asyncio.sleep(0.01)


async def _next_turn_completed(session: AppServerSession) -> None:
    async for event in session.events():
        if isinstance(event, TurnCompleted):
            return


@pytest.mark.asyncio
async def test_a_config_the_schema_refuses_fails_session_start_with_its_reason(
    config_dir: Path,
) -> None:
    """*Prepare*: A saved config with ``auto_compact_threshold = 0``.
    *Do*: Open a session.
    *Assert*: Invalid params of kind configuration, carrying the validator's
    reason without pydantic's prefix, so clients show what to fix.
    """
    # Prepare
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["auto_compact_threshold"] = 0
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(), capabilities=ClientCapabilities()
    )

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await connection.host.open_session()

    # Assert
    error = exc_info.value.error
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert error.message == (
        "Cannot load the Vibe config. auto_compact_threshold: 0 is not allowed. "
        'There is no "off" value: set a positive threshold, or remove the '
        "setting to use the model's default."
    )


@pytest.mark.asyncio
async def test_a_config_write_the_schema_refuses_is_rejected_with_its_reason(
    backend_contract_session: AppServerSession,
) -> None:
    """*Prepare*: A running session.
    *Do*: Write only the bash default timeout, which needs its maximum too.
    *Assert*: The write is refused with the schema's reason, so clients show
    what to fix rather than a bare "Invalid configuration edit".
    """
    # Do
    with pytest.raises(AppServerResponseError) as refused:
        await backend_contract_session.resources.config.update({
            "tools": {"bash": {"default_timeout": 30}}
        })

    # Assert
    assert refused.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert refused.value.error.message == (
        "Invalid configuration edit: tools: tools.bash: default_timeout is set"
        " without max_timeout_seconds. Set both, or neither."
    )
