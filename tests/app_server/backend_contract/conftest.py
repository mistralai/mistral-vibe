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

from tests.constants import (
    CHAT_COMPLETIONS_PATH,
    CONNECTORS_BOOTSTRAP_PATH,
    MISTRAL_BASE_URL,
)
from tests.stubs.mistral_responses import mistral_completion
from vibe.app_server._account import AccountGateway
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
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


class _PartialThenGatedSSEStream(httpx.AsyncByteStream):
    """Say part of the answer, then hang until released.

    The gate opens only once the model has really produced visible text, which
    is the state a mid-answer interrupt has to be judged against: something is
    already on screen and the turn is still open.
    """

    def __init__(
        self,
        partial: bytes,
        remainder: bytes,
        started: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._partial = partial
        self._remainder = remainder
        self._started = started
        self._release = release

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._partial
        self._started.set()
        await self._release.wait()
        yield self._remainder


async def connect_backend_contract_host(
    *,
    session_options: SessionOptions,
    capabilities: ClientCapabilities,
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
    process_services: ProcessServices,
    process: HarnessProcess | None = None,
) -> BackendContractConnection:
    client_transport, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        account_gateway=account_gateway,
        identity_gateway=identity_gateway,
        process=process or HarnessProcess(services=process_services),
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
    *,
    session_options: SessionOptions,
    capabilities: ClientCapabilities,
    process_services: ProcessServices,
) -> AppServerClient:
    client_transport, server_transport = memory_transport_pair()
    harness = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
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


_opened_backends: list[Any] = []


@pytest.fixture(autouse=True)
def _record_opened_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )

    _opened_backends.clear()
    initialize = UnifiedHarnessBackendAdapter.initialize_integrations

    async def recording(self: UnifiedHarnessBackendAdapter) -> None:
        _opened_backends.append(self)
        await initialize(self)

    monkeypatch.setattr(
        UnifiedHarnessBackendAdapter, "initialize_integrations", recording
    )


async def wait_for_background_integrations() -> None:
    """Wait for the connector and MCP loads every opened session runs after open.

    ``session/ready/wait`` does not cover them: turns never wait on them.
    """
    for backend in list(_opened_backends):
        for task in (backend._connector_resolve_task, backend._mcp_discovery_task):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
def backend_contract_session_options() -> SessionOptions:
    return SessionOptions()


@pytest.fixture
def backend_contract_capabilities() -> ClientCapabilities:
    return ClientCapabilities()


@pytest.fixture(autouse=True)
def _mock_identity_endpoint(respx_mock: respx.MockRouter) -> None:
    # The connector read path resolves the caller's identity (for the console
    # manage-connectors link) via a module-global cache that issues a real
    # request. Stub it so strict-RESPX contract tests don't fail on it; 401 makes
    # identity resolve to None, which the projection assertions don't depend on.
    respx_mock.get(f"{MISTRAL_BASE_URL}/v1/users/me").mock(
        return_value=httpx.Response(401)
    )
    # Same for /whoami, read for the private-connectors warning.
    respx_mock.get("https://console.mistral.ai/api/vibe/whoami").mock(
        return_value=httpx.Response(401)
    )


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
def backend_contract_partial_mistral_response() -> Callable[..., httpx.Response]:
    """A provider that streams ``partial``, then holds the turn open."""

    def build(
        partial: str, *, started: asyncio.Event, release: asyncio.Event
    ) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_PartialThenGatedSSEStream(
                _mistral_sse_delta(partial), _mistral_sse_terminator(), started, release
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    return build


def _mistral_sse_delta(content: str) -> bytes:
    """One streamed delta, with no finish reason: the answer is still coming."""
    response = mistral_completion(content, tool_calls=None)
    response["model"] = "mistral-vibe-cli-latest"
    response["object"] = "chat.completion.chunk"
    choice = response["choices"][0]
    message = choice.pop("message")
    message.pop("tool_calls", None)
    choice["delta"] = message
    choice["finish_reason"] = None
    return b"data: " + json.dumps(response).encode() + b"\n\n"


def _mistral_sse_terminator() -> bytes:
    """The closing chunk: no new text, just a finish reason.

    Deltas concatenate, so anything with content here would be appended to what
    the stream already said rather than replacing it.
    """
    response = mistral_completion("", tool_calls=None)
    response["model"] = "mistral-vibe-cli-latest"
    response["object"] = "chat.completion.chunk"
    choice = response["choices"][0]
    _ = choice.pop("message")
    choice["delta"] = {}
    choice["finish_reason"] = "stop"
    return b"data: " + json.dumps(response).encode() + b"\n\ndata: [DONE]"


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
    backend_contract_session_options: SessionOptions,
    backend_contract_capabilities: ClientCapabilities,
    process_services: ProcessServices,
) -> AsyncIterator[AppServerHost]:
    connection = await connect_backend_contract_host(
        session_options=backend_contract_session_options,
        capabilities=backend_contract_capabilities,
        process_services=process_services,
    )
    try:
        yield connection.host
    finally:
        await connection.host.close()


@pytest_asyncio.fixture
async def backend_contract_connection(
    backend_contract_session_options: SessionOptions,
    backend_contract_capabilities: ClientCapabilities,
    process_services: ProcessServices,
) -> AsyncIterator[BackendContractConnection]:
    connection = await connect_backend_contract_host(
        session_options=backend_contract_session_options,
        capabilities=backend_contract_capabilities,
        process_services=process_services,
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


@pytest.fixture
def backend_contract_persistent_config(config_dir: Path, tmp_path: Path) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {
        "enabled": True,
        "save_dir": str(tmp_path / "sessions"),
    }
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest_asyncio.fixture
async def backend_contract_persistent_connection(
    backend_contract_persistent_config: None, process_services: ProcessServices
) -> AsyncIterator[BackendContractConnection]:
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
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
