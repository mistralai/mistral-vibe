"""Run channels: a multiplexed app-server serving ``vibe run-server``'s runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
import logging
from pathlib import Path
import traceback
from typing import Any

import pytest
import pytest_asyncio

from tests.stubs.fake_agent_server import FakeAgentServer, private_socket_path
from vibe.app_server import _process_settings, _run_channel
from vibe.app_server._agent_socket import OUTCOME_ERROR_FIELD, AgentSocketMount
from vibe.app_server._process_settings import ProcessSettings
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.multiplex import (
    ChannelOpen,
    ChannelProtocolError,
    ChannelServer,
    serve_multiplexed,
)
from vibe.app_server.stdio import serve_stdio
from vibe.app_server.transport import (
    JsonRpcTransport,
    StdioJsonRpcTransport,
    memory_transport_pair,
)
from vibe.observability.logging import StructuredLogFormatter, logger
from vibe.utils.api_keys import resolve_api_key_with_origin
from vibe.utils.secrets import REDACTED

_CONFIG = """
active_model = "fake-model"
include_project_context = false
[experiments]
enable = false
[telemetry]
enable = false
[[providers]]
name = "fake"
api_base = "http://127.0.0.1:1/v1"
api_key_env_var = "RUN_CHANNEL_KEY"
api_style = "openai"
[[models]]
name = "fake-model"
provider = "fake"
alias = "fake-model"
thinking = "off"
"""


# The run's key: the process has none, so a session opens only with it.
_KEY = {"RUN_CHANNEL_KEY": "run-secret"}


def _open(socket: Path | str = "/tmp/none.sock", **fields: Any) -> dict[str, Any]:
    return {"runId": "run-1", "agentSocket": str(socket), **fields}


async def _serve_until_error(frames: list[dict[str, Any]]) -> BaseException:
    client, server = memory_transport_pair()

    async def create_channel(
        channel: JsonRpcTransport, _options: ChannelOpen | None
    ) -> ChannelServer:
        class Idle:
            async def serve(self) -> None:
                async for _ in channel.messages():
                    pass

        return Idle()

    serving = asyncio.create_task(serve_multiplexed(server, create_channel))
    for frame in frames:
        await client.send(frame)
    with pytest.raises(BaseException) as exc_info:
        await serving
    await client.close()
    return exc_info.value


def _only(error: BaseException) -> BaseException:
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


@pytest.mark.asyncio
async def test_a_malformed_open_frame_names_its_fields_and_never_their_values() -> None:
    """*Prepare*: An open frame whose config is not an object, with an unknown
    field and a credential that is not a string, all carrying secrets.
    *Do*: Serve it.
    *Assert*: The stream ends with a protocol error naming each field; neither
    its message nor its traceback holds a secret.
    """
    frame = {
        "channelId": "1",
        "message": {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        "open": _open(
            config="sk-config-secret",
            apiKey="sk-extra-secret",
            credentials={"GOOD_KEY": "sk-good-secret", "BAD_KEY": 42},
        ),
    }

    error = _only(await _serve_until_error([frame]))

    assert isinstance(error, ChannelProtocolError)
    for field in ("open.config", "open.apiKey", "open.credentials.BAD_KEY"):
        assert field in str(error)
    rendered = "".join(traceback.format_exception(error))
    for secret in ("sk-config-secret", "sk-extra-secret", "sk-good-secret", "42"):
        assert secret not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frames",
    [
        pytest.param(
            [
                {"channelId": "1", "message": {"id": 1}},
                {"channelId": "1", "message": {"id": 2}, "open": _open()},
            ],
            id="later-frame",
        ),
        pytest.param(
            [{"channelId": "1", "message": None, "open": _open()}], id="no-message"
        ),
    ],
)
async def test_open_belongs_only_on_the_frame_that_opens_the_channel(
    frames: list[dict[str, Any]],
) -> None:
    error = _only(await _serve_until_error(frames))

    assert isinstance(error, ChannelProtocolError)
    assert "open is allowed only on the frame that opens the channel" in str(error)


@pytest.mark.asyncio
async def test_each_run_channel_holds_its_own_credentials_and_redacts_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: Two run channels, each with its own key for one variable, the
    process holding another; the second channel's server fails naming its key.
    *Do*: Ask each for the key it resolves, in a worker thread as the
    credential port does.
    *Assert*: Each resolves its own; the failed channel's close error and log
    line hide its key.
    """
    # Prepare
    monkeypatch.setenv("RUN_CHANNEL_KEY", "process-secret")
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(handler.format(record))  # type: ignore[method-assign]
    handler.setFormatter(StructuredLogFormatter())
    logger.addHandler(handler)
    client, server = memory_transport_pair()

    async def create_channel(
        channel: JsonRpcTransport, options: ChannelOpen | None
    ) -> ChannelServer:
        assert options is not None

        class KeyServer:
            async def serve(self) -> None:
                async for message in channel.messages():
                    resolved = await asyncio.to_thread(
                        resolve_api_key_with_origin, "RUN_CHANNEL_KEY"
                    )
                    assert resolved is not None
                    if message.get("fail"):
                        raise RuntimeError(f"refused {resolved[0]}")
                    await channel.send({"key": resolved[0]})

        return KeyServer()

    serving = asyncio.create_task(serve_multiplexed(server, create_channel))
    incoming = client.messages()
    try:
        # Do
        for channel, key in (("1", "run-a-secret"), ("2", "run-b-secret")):
            await client.send({
                "channelId": channel,
                "message": {"ask": True},
                "open": _open(credentials={"RUN_CHANNEL_KEY": key}),
            })
        answers = {
            frame["channelId"]: frame["message"]
            for frame in [await anext(incoming), await anext(incoming)]
        }
        await client.send({"channelId": "2", "message": {"fail": True}})
        close = await anext(incoming)
    finally:
        logger.removeHandler(handler)
        await client.close()
        with suppress(Exception):
            await serving

    # Assert
    assert answers == {"1": {"key": "run-a-secret"}, "2": {"key": "run-b-secret"}}
    assert close == {"channelId": "2", "message": None, "error": f"refused {REDACTED}"}
    logged = "\n".join(records)
    assert "run=run-1 channel=2 App-server channel failed" in logged
    assert f"refused {REDACTED}" in logged
    assert "run-b-secret" not in logged


def test_a_run_config_with_another_trust_store_fails_and_another_log_level_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """*Prepare*: Process settings from the server's config.
    *Do*: Check a run config with another trust store, then one with another
    log level.
    *Assert*: The first fails as config_error; the second logs and becomes the
    run's warning.
    """
    from tests.conftest import build_test_vibe_config

    settings = ProcessSettings(log_level="WARNING", enable_system_trust_store=False)

    with pytest.raises(SessionBackendError) as exc_info:
        settings.check(build_test_vibe_config(enable_system_trust_store=True))
    with caplog.at_level(logging.WARNING, logger=logger.name):
        settings.check(build_test_vibe_config(log_level="DEBUG"))
    issues = settings.issues(build_test_vibe_config(log_level="DEBUG"))

    assert exc_info.value.data == {OUTCOME_ERROR_FIELD: "config_error"}
    assert "enable_system_trust_store" in str(exc_info.value)
    assert [issue.message for issue in issues] == [
        "The run's config sets log_level 'DEBUG', but this app-server logs at "
        "'WARNING', from its own config: the log level applies to the whole "
        "process, so to every run."
    ]
    assert issues[0].message in caplog.text
    assert settings.issues(build_test_vibe_config(log_level="WARNING")) == []


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Process settings start unpinned and pin nothing process-wide."""
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(_process_settings, "_pinned", None)
    monkeypatch.setattr(
        "vibe.utils.http.pin_ssl_context",
        lambda *, enable_system_trust_store: calls.append((
            "trust_store",
            enable_system_trust_store,
        )),
    )
    monkeypatch.setattr(
        "vibe.observability.logging.set_config_log_level",
        lambda level: calls.append(("log_level", level)),
    )
    return calls


@pytest.mark.asyncio
async def test_process_settings_pin_once_from_the_servers_own_config(
    config_dir: Path, pinned: list[tuple[str, object]]
) -> None:
    (config_dir / "config.toml").write_text(
        'log_level = "ERROR"\nenable_system_trust_store = true\n' + _CONFIG
    )

    first = await _process_settings.pin_process_settings()
    second = await _process_settings.pin_process_settings()

    assert first == second == ProcessSettings("ERROR", enable_system_trust_store=True)
    assert pinned == [("trust_store", True), ("log_level", "ERROR")]


class _RunClient:
    def __init__(self, transport: JsonRpcTransport) -> None:
        self.transport = transport
        self.incoming = transport.messages()
        self.next_id = 0
        self.closed: dict[str, dict[str, Any]] = {}

    async def call(
        self,
        channel: str,
        method: str,
        params: dict[str, Any],
        *,
        open: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        frame: dict[str, Any] = {
            "channelId": channel,
            "message": {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        }
        if open is not None:
            frame["open"] = open
        await self.transport.send(frame)
        async for frame in self.incoming:
            message = frame["message"]
            if message is None:
                self.closed[frame["channelId"]] = frame
                continue
            if message.get("id") == request_id:
                assert frame["channelId"] == channel
                return message
        raise AssertionError("Server closed before responding")

    async def start(self, channel: str, socket: Path, **open: Any) -> dict[str, Any]:
        await self.call(
            channel,
            "initialize",
            {"clientInfo": {"name": "run-channel-test", "version": "1"}},
            open=_open(socket, **open),
        )
        await self.transport.send({
            "channelId": channel,
            "message": {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        })
        return await self.call(
            channel, "session/start", {"agentConfig": {"headless": True}}
        )

    async def close(self, channel: str) -> dict[str, Any]:
        await self.transport.send({"channelId": channel, "message": None})
        async for frame in self.incoming:
            if frame["channelId"] == channel and frame["message"] is None:
                return frame
        raise AssertionError("Server closed before acknowledging")


@pytest.fixture
def sockets() -> Iterator[tuple[Path, Path]]:
    with private_socket_path() as first, private_socket_path() as second:
        yield first, second


@pytest_asyncio.fixture
async def run_client(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, pinned: list[tuple[str, object]]
) -> AsyncIterator[_RunClient]:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    (config_dir / "config.toml").write_text(_CONFIG)
    peer, transport = memory_transport_pair()
    monkeypatch.setattr(
        StdioJsonRpcTransport, "from_standard_streams", lambda: transport
    )
    serving = asyncio.create_task(serve_stdio(multiplex=True))
    try:
        yield _RunClient(peer)
    finally:
        await peer.close()
        await serving


def _outcome(response: dict[str, Any]) -> str | None:
    assert "error" in response, response
    return response["error"].get("data", {}).get(OUTCOME_ERROR_FIELD)


@pytest.mark.asyncio
async def test_an_abort_on_one_runs_socket_leaves_the_others_run_open(
    run_client: _RunClient, sockets: tuple[Path, Path], tmp_path: Path
) -> None:
    """*Prepare*: Two agent servers with sandboxes; the first aborts its run
    at the handshake.
    *Do*: Open a run channel on each, then a session.
    *Assert*: The first run's session open fails as aborted; the second's
    session opens in its own sandbox's workspace.
    """
    first_workspace, second_workspace = tmp_path / "a", tmp_path / "b"
    first_workspace.mkdir()
    second_workspace.mkdir()
    aborting = FakeAgentServer(
        sockets[0], first_workspace, aborts={"agent/initialize": "Out of budget"}
    )
    healthy = FakeAgentServer(sockets[1], second_workspace)

    async with aborting, healthy:
        failed = await run_client.start("1", sockets[0], credentials=_KEY)
        opened = await run_client.start("2", sockets[1], credentials=_KEY)

    assert _outcome(failed) == "aborted"
    assert "Out of budget" in failed["error"]["message"]
    assert "error" not in opened, opened
    assert opened["result"]["state"]["session"]["cwd"] == str(second_workspace)


@pytest.mark.asyncio
async def test_a_socket_without_a_sandbox_fails_the_run_as_a_usage_error(
    run_client: _RunClient, sockets: tuple[Path, Path]
) -> None:
    async with FakeAgentServer(sockets[0], None):
        response = await run_client.start("1", sockets[0], credentials=_KEY)

    assert _outcome(response) == "usage_error"
    assert "serves no sandbox" in response["error"]["message"]


@pytest.mark.asyncio
async def test_a_run_whose_config_sets_another_trust_store_fails_as_config_error(
    run_client: _RunClient, sockets: tuple[Path, Path], tmp_path: Path
) -> None:
    async with FakeAgentServer(sockets[0], tmp_path):
        response = await run_client.start(
            "1",
            sockets[0],
            config={"active_model": "fake-model", "enable_system_trust_store": True},
            credentials=_KEY,
        )

    assert _outcome(response) == "config_error"


@pytest.mark.asyncio
async def test_closing_a_run_channel_unsubscribes_its_mount(
    run_client: _RunClient,
    sockets: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: Record the mount each run channel connects, and how many
    app servers were subscribed to it when it closed.
    *Do*: Open a session on a run channel, then close the channel.
    *Assert*: The mount was subscribed while the run lasted and holds no
    notifier after.
    """
    mounts: list[AgentSocketMount] = []
    subscribed: list[int] = []
    connect = AgentSocketMount.connect
    close = AgentSocketMount.close

    async def recording_connect(path: Path) -> AgentSocketMount:
        mount = await connect(path)
        mounts.append(mount)
        return mount

    def recording_close(mount: AgentSocketMount) -> None:
        subscribed.append(len(mount._notifiers))
        close(mount)

    monkeypatch.setattr(
        _run_channel.AgentSocketMount, "connect", staticmethod(recording_connect)
    )
    monkeypatch.setattr(_run_channel.AgentSocketMount, "close", recording_close)

    async with FakeAgentServer(sockets[0], tmp_path):
        opened = await run_client.start("1", sockets[0], credentials=_KEY)
        acknowledged = await run_client.close("1")

    assert "error" not in opened, opened
    assert "error" not in acknowledged
    assert len(mounts) == 1
    assert subscribed[0] > 0
    assert mounts[0]._notifiers == []
