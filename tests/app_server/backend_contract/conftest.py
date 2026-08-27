from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
import json
from pathlib import Path
import tomllib
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
import tomli_w

from tests.backend.data.mistral import mistral_completion
from tests.constants import (
    CHAT_COMPLETIONS_PATH,
    CONNECTORS_BOOTSTRAP_PATH,
    MISTRAL_BASE_URL,
)
from vibe.app_server._runtime import create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.host import AppServerHost
from vibe.app_server.protocol import ClientCapabilities, ClientInfo, SessionOptions
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair


@dataclass(frozen=True, slots=True)
class BackendContractConnection:
    client: AppServerClient
    host: AppServerHost


class _GatedSSEStream(httpx.AsyncByteStream):
    def __init__(
        self, payload: bytes, started: asyncio.Event, release: asyncio.Event
    ) -> None:
        self._payload = payload
        self._started = started
        self._release = release

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._started.set()
        await self._release.wait()
        yield self._payload


async def connect_backend_contract_host(
    experimental_harness: bool,
    *,
    session_options: SessionOptions,
    capabilities: ClientCapabilities,
) -> BackendContractConnection:
    client_transport, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        experimental_harness=experimental_harness,
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    host = await AppServerHost.connect(
        client,
        client_info=ClientInfo(name="backend-contract-test", version="0"),
        capabilities=capabilities,
        session_options=session_options,
        client_factory=harness.connect_client,
    )
    return BackendContractConnection(client=client, host=host)


async def connect_backend_contract_client(
    experimental_harness: bool,
    *,
    session_options: SessionOptions,
    capabilities: ClientCapabilities,
) -> AppServerClient:
    client_transport, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        experimental_harness=experimental_harness,
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    try:
        await client.start()
        await client.initialize(
            ClientInfo(name="backend-contract-test", version="0"), capabilities
        )
        await client.notify("initialized")
    except BaseException:
        await client.close()
        raise
    return client


@pytest.fixture
def backend_contract_session_options() -> SessionOptions:
    return SessionOptions()


@pytest.fixture
def backend_contract_capabilities() -> ClientCapabilities:
    return ClientCapabilities()


@pytest.fixture
def backend_contract_mistral_api(respx_mock: respx.MockRouter) -> respx.Route:
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json={"connectors": []})
    )
    return respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}")


@pytest.fixture
def backend_contract_mistral_response() -> Callable[..., httpx.Response]:
    def build(
        content: str, *, tool_calls: list[dict[str, Any]] | None = None
    ) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(stream=_mistral_sse_payload(content, tool_calls)),
            headers={"Content-Type": "text/event-stream"},
        )

    return build


@pytest.fixture
def backend_contract_gated_mistral_response() -> Callable[..., httpx.Response]:
    def build(
        content: str, *, started: asyncio.Event, release: asyncio.Event
    ) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_GatedSSEStream(
                _mistral_sse_payload(content, tool_calls=None), started, release
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    return build


@pytest.fixture
def backend_contract_mistral_completion() -> Callable[[str], httpx.Response]:
    def build(content: str) -> httpx.Response:
        response = mistral_completion(content)
        response["model"] = "mistral-vibe-cli-latest"
        return httpx.Response(200, json=response)

    return build


def _mistral_sse_payload(
    content: str, tool_calls: list[dict[str, Any]] | None
) -> bytes:
    response = mistral_completion(content, tool_calls=tool_calls)
    response["model"] = "mistral-vibe-cli-latest"
    response["object"] = "chat.completion.chunk"
    choice = response["choices"][0]
    message = choice.pop("message")
    if tool_calls is None:
        message.pop("tool_calls")
    choice["delta"] = message
    return b"data: " + json.dumps(response).encode() + b"\n\ndata: [DONE]"


@pytest_asyncio.fixture
async def backend_contract_host(
    experimental_harness: bool,
    backend_contract_session_options: SessionOptions,
    backend_contract_capabilities: ClientCapabilities,
) -> AsyncIterator[AppServerHost]:
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=backend_contract_session_options,
        capabilities=backend_contract_capabilities,
    )
    try:
        yield connection.host
    finally:
        await connection.host.close()


@pytest_asyncio.fixture
async def backend_contract_connection(
    experimental_harness: bool,
    backend_contract_session_options: SessionOptions,
    backend_contract_capabilities: ClientCapabilities,
) -> AsyncIterator[BackendContractConnection]:
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=backend_contract_session_options,
        capabilities=backend_contract_capabilities,
    )
    try:
        yield connection
    finally:
        await connection.host.close()


@pytest_asyncio.fixture
async def backend_contract_session(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api: respx.Route,
) -> AsyncIterator[AppServerSession]:
    session = await backend_contract_connection.host.open_session()
    try:
        yield session
    finally:
        await session.close()


@pytest_asyncio.fixture
async def backend_contract_persistent_connection(
    config_dir: Path, experimental_harness: bool, tmp_path: Path
) -> AsyncIterator[BackendContractConnection]:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {
        "enabled": True,
        "save_dir": str(tmp_path / "sessions"),
    }
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
    )
    try:
        yield connection
    finally:
        await connection.host.close()


@pytest_asyncio.fixture
async def backend_contract_persistent_session(
    backend_contract_mistral_api: respx.Route,
    backend_contract_persistent_connection: BackendContractConnection,
) -> AsyncIterator[AppServerSession]:
    session = await backend_contract_persistent_connection.host.open_session()
    try:
        yield session
    finally:
        await session.close()
