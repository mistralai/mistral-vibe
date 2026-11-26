"""Vibe's commands against a Session of the target runtime, through the test bridge.

See ``tests/app_server/target_session_bridge.py`` for what the bridge stands in
for and what it leaves out.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import timedelta
import json
from pathlib import Path
import tomllib
import uuid

import httpx
import pytest
import pytest_asyncio
import respx

from mistralai_vibe_local_harness.vibe import (
    UnifiedHarnessSessionBackend,
    UnifiedHarnessSessionBackendHost,
)
from tests.app_server.backend_contract.conftest import connect_backend_contract_client
from tests.app_server.target_session_bridge import (
    TARGET_ANSWERS,
    TARGET_COMMANDS,
    TARGET_WRITE_TOOL,
    TargetCommandsTransport,
    TargetRuntime,
    TargetSession,
    target_model_facts,
)
from vibe.app_server import _unified_harness_backend_adapter as adapter_module
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server._target_session_errors import target_refusals
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import TextContentBlock
from vibe.app_server.protocol import (
    AppServerResponseError,
    CallbackCallParams,
    CallbackCallResponse,
    CallbackResult,
    CallbackResultParams,
    ClientCapabilities,
    ProtocolError,
    ProtocolErrorCode,
    ServerRequest,
    SessionOptions,
    SessionStartParams,
    SessionStartResponse,
    TurnInterruptParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
)
from vibe.core.tools.builtins.bash import BashToolConfig

SHORT_CALLBACK_TIMEOUT = timedelta(milliseconds=300)
"""A deadline a test waits out, for the tests about a timed-out callback."""

pytestmark = pytest.mark.one_path


@pytest.fixture
def callback_timeout() -> timedelta:
    """How long a target callback waits for its answer.

    Long, so a callback a test answers stays open however slow the machine;
    a test about a timed-out callback parametrizes it short.
    """
    return timedelta(minutes=5)


@pytest.fixture
def target_model(backend_contract_mistral_api: respx.Route) -> Iterator[respx.Route]:
    """The completion route of the target runtime; script it with ``mock``.

    Vibe's own Session shares it, but the bridge never runs a Turn there.
    """
    with target_model_facts():
        yield backend_contract_mistral_api


@pytest_asyncio.fixture
async def target_runtime(
    tmp_path: Path, callback_timeout: timedelta
) -> AsyncIterator[TargetRuntime]:
    runtime = TargetRuntime(tmp_path / "target", callback_timeout=callback_timeout)
    try:
        yield runtime
    finally:
        await runtime.close()


@pytest.fixture
def forwarded() -> frozenset[str]:
    """What the bridge sends to the target; a test parametrizes it to keep more local."""
    return TARGET_COMMANDS


@pytest.fixture
def callback_ids() -> dict[str, str]:
    """The target callback each Vibe callback's answer goes to; a test fills it."""
    return {}


@pytest_asyncio.fixture
async def target_session(
    target_model: respx.Route,
    target_runtime: TargetRuntime,
    forwarded: frozenset[str],
    callback_ids: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> TargetSession:
    """A target Session that gets the commands of the next Session Vibe opens."""
    target = await target_runtime.start_session()

    def commands(
        session: UnifiedHarnessSessionBackend,
        host: UnifiedHarnessSessionBackendHost,
        _derivation: object,
    ) -> SessionApiCommands:
        transport = TargetCommandsTransport(
            session,
            children=host,
            target=target,
            forwarded=forwarded,
            callback_ids=callback_ids,
        )
        return SessionApiCommands(
            session,
            host,
            transport=transport,
            refusals=target_refusals,
            grant_before_answer=False,
        )

    monkeypatch.setattr(adapter_module, "_session_api_commands", commands)
    return target


@pytest_asyncio.fixture
async def vibe_client(
    target_session: TargetSession, process_services: ProcessServices
) -> AsyncIterator[AppServerClient]:
    client = await connect_backend_contract_client(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        yield client
    finally:
        await client.close()


async def _start_vibe_session(client: AppServerClient) -> str:
    started = SessionStartResponse.model_validate(
        await client.request("session/start", SessionStartParams())
    )
    return started.state.session.id


@pytest.mark.asyncio
async def test_a_turn_start_runs_the_turn_on_the_target_session(
    vibe_client: AppServerClient,
    target_session: TargetSession,
    target_model: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    target_model.mock(
        return_value=backend_contract_mistral_response("Hello from the target")
    )
    session_id = await _start_vibe_session(vibe_client)

    started = TurnStartResponse.model_validate(
        await vibe_client.request(
            "turn/start",
            TurnStartParams(
                session_id=session_id, message=[TextContentBlock(text="hi")]
            ),
        )
    )
    await target_session.wait_until_idle()

    target_turns = (await target_session.read())["state"]["turns"]["items"]
    assert [turn["id"] for turn in target_turns] == [started.turn.id]
    assert target_model.called


def _write_file_call(build: Callable[..., httpx.Response]) -> httpx.Response:
    """A model answer that calls the target's write tool, which asks for approval."""
    arguments = json.dumps({"path": "note.txt", "content": "from the model"})
    return build(
        "",
        tool_calls=[
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": TARGET_WRITE_TOOL, "arguments": arguments},
            }
        ],
    )


async def _start_turn(client: AppServerClient, session_id: str) -> str:
    started = TurnStartResponse.model_validate(
        await client.request(
            "turn/start",
            TurnStartParams(
                session_id=session_id, message=[TextContentBlock(text="write it")]
            ),
        )
    )
    return started.turn.id


async def _refused(request: Awaitable[object]) -> ProtocolError:
    with pytest.raises(AppServerResponseError) as exc_info:
        await request
    return exc_info.value.error


@pytest_asyncio.fixture
async def waiting_turn(
    vibe_client: AppServerClient,
    target_session: TargetSession,
    target_model: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> tuple[str, str, str]:
    """A Vibe Session whose target Turn waits for an approval.

    Returns the Vibe Session ID, the Turn ID and the target callback's ID.
    """
    target_model.mock(
        side_effect=[
            _write_file_call(backend_contract_mistral_response),
            backend_contract_mistral_response("Done"),
        ]
    )
    session_id = await _start_vibe_session(vibe_client)
    turn_id = await _start_turn(vibe_client, session_id)
    return session_id, turn_id, await target_session.open_callback_id()


def _approval(session_id: str, callback_id: str, choice: str) -> CallbackResultParams:
    return CallbackResultParams(
        session_id=session_id,
        result=CallbackResult(
            callback_id=callback_id,
            output={"type": "approval", "decision": {"type": choice}},
        ),
    )


@pytest.mark.asyncio
async def test_a_turn_start_while_the_target_runs_a_turn_is_a_turn_conflict(
    vibe_client: AppServerClient, waiting_turn: tuple[str, str, str]
) -> None:
    session_id, turn_id, _ = waiting_turn

    error = await _refused(_start_turn(vibe_client, session_id))

    assert error.code is ProtocolErrorCode.CONFLICT
    assert error.data == {
        "harnessCode": "turn_conflict",
        "details": {"active_turn_id": turn_id},
    }


@pytest.mark.asyncio
async def test_a_steer_that_names_another_turn_is_stale_and_names_the_active_one(
    vibe_client: AppServerClient, waiting_turn: tuple[str, str, str]
) -> None:
    session_id, turn_id, _ = waiting_turn

    error = await _refused(
        vibe_client.request(
            "turn/steer",
            TurnSteerParams(
                session_id=session_id,
                expected_turn_id="another-turn",
                message=[TextContentBlock(text="also this")],
            ),
        )
    )

    assert error.code is ProtocolErrorCode.STALE_TURN
    assert error.data == {"activeTurnId": turn_id}


@pytest.mark.asyncio
async def test_an_interrupt_that_names_another_turn_is_stale_and_names_the_active_one(
    vibe_client: AppServerClient, waiting_turn: tuple[str, str, str]
) -> None:
    session_id, turn_id, _ = waiting_turn

    error = await _refused(
        vibe_client.request(
            "turn/interrupt",
            TurnInterruptParams(session_id=session_id, expected_turn_id="another-turn"),
        )
    )

    assert error.code is ProtocolErrorCode.STALE_TURN
    assert error.data == {"activeTurnId": turn_id}


@pytest.mark.asyncio
async def test_an_interrupt_with_no_active_turn_is_a_conflict(
    vibe_client: AppServerClient,
    target_session: TargetSession,
    target_model: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    target_model.mock(return_value=backend_contract_mistral_response("Done"))
    session_id = await _start_vibe_session(vibe_client)
    turn_id = await _start_turn(vibe_client, session_id)
    await target_session.wait_until_idle()

    error = await _refused(
        vibe_client.request(
            "turn/interrupt",
            TurnInterruptParams(session_id=session_id, expected_turn_id=turn_id),
        )
    )

    assert error.code is ProtocolErrorCode.CONFLICT
    assert error.message == "No active turn"


@pytest.mark.asyncio
async def test_an_answer_the_target_rejects_is_invalid_and_a_corrected_one_is_accepted(
    vibe_client: AppServerClient,
    target_session: TargetSession,
    target_runtime: TargetRuntime,
    waiting_turn: tuple[str, str, str],
) -> None:
    session_id, _, callback_id = waiting_turn
    # Not a Vibe approval, so the bridge passes it as it is: a scope the target
    # does not know.
    rejected = CallbackResultParams(
        session_id=session_id,
        result=CallbackResult(
            callback_id=callback_id, output={"decision": "allow", "scope": "always"}
        ),
    )

    error = await _refused(vibe_client.request("callback/result", rejected))
    still_open = await target_session.active_callback_ids()
    await vibe_client.request(
        "callback/result", _approval(session_id, callback_id, "approve")
    )
    await target_session.wait_until_idle()

    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert still_open == [callback_id]
    note = target_runtime.workspace / "note.txt"
    assert note.read_text(encoding="utf-8") == "from the model"


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_timeout", [SHORT_CALLBACK_TIMEOUT])
async def test_an_answer_after_the_target_callback_timed_out_is_callback_closed(
    vibe_client: AppServerClient,
    target_session: TargetSession,
    waiting_turn: tuple[str, str, str],
) -> None:
    session_id, _, callback_id = waiting_turn
    resolved = await target_session.wait_until_callback_resolved(callback_id)

    error = await _refused(
        vibe_client.request(
            "callback/result", _approval(session_id, callback_id, "approve")
        )
    )

    assert resolved["outcome"]["error"]["code"] == "callback_timed_out"
    assert error.code is ProtocolErrorCode.CALLBACK_CLOSED


@pytest.mark.asyncio
async def test_another_answer_to_an_answered_target_callback_is_accepted_and_ignored(
    vibe_client: AppServerClient,
    target_session: TargetSession,
    target_runtime: TargetRuntime,
    waiting_turn: tuple[str, str, str],
) -> None:
    """Option B: the target takes the first answer and accepts later ones.

    The later answer differs from the first, which the local runtime would
    refuse; it changes nothing.
    """
    session_id, _, callback_id = waiting_turn
    await vibe_client.request(
        "callback/result", _approval(session_id, callback_id, "deny")
    )
    await target_session.wait_until_idle()

    await vibe_client.request(
        "callback/result", _approval(session_id, callback_id, "approve")
    )

    resolutions = [
        event["outcome"]
        for event in await target_session.stored_events()
        if event["type"] == "callback_resolved" and event["callbackId"] == callback_id
    ]
    assert resolutions == [{"outcome": "answered", "output": {"decision": "deny"}}]
    assert not (target_runtime.workspace / "note.txt").exists()


@pytest.mark.asyncio
async def test_a_turn_start_that_reuses_a_key_with_other_input_is_a_conflict(
    vibe_client: AppServerClient,
    target_session: TargetSession,
    target_model: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    target_model.mock(return_value=backend_contract_mistral_response("Done"))
    session_id = await _start_vibe_session(vibe_client)

    def start(text: str) -> Awaitable[object]:
        return vibe_client.request(
            "turn/start",
            TurnStartParams(
                session_id=session_id,
                idempotency_key="key-1",
                message=[TextContentBlock(text=text)],
            ),
        )

    await start("first")
    await target_session.wait_until_idle()
    error = await _refused(start("second"))

    assert error.code is ProtocolErrorCode.CONFLICT
    assert error.data == {"idempotencyKey": "key-1"}


@pytest.mark.asyncio
async def test_an_answer_to_a_callback_the_target_does_not_know_is_an_internal_error(
    vibe_client: AppServerClient, waiting_turn: tuple[str, str, str]
) -> None:
    """A known gap: the target's refusal is untyped, so Vibe cannot say not found."""
    session_id, _, _ = waiting_turn
    # A well-formed ID, so the target's lookup is what refuses it.
    unknown = str(uuid.uuid4())

    error = await _refused(
        vibe_client.request(
            "callback/result", _approval(session_id, unknown, "approve")
        )
    )

    assert error.code is ProtocolErrorCode.INTERNAL_ERROR


def _model_answers(
    build: Callable[..., httpx.Response],
) -> Callable[..., httpx.Response]:
    """Answers for both Sessions on the shared route: a tool call, then "Done".

    Vibe's Session gets a bash call, which asks for approval in Vibe; the
    target's gets its write tool, which asks in the target.
    """

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["messages"][-1]["role"] != "user":
            return build("Done")
        names = {tool["function"]["name"] for tool in body.get("tools") or []}
        if "bash" not in names:
            return _write_file_call(build)
        command = json.dumps({"command": "touch granted", "timeout_seconds": 5})
        return build(
            "",
            tool_calls=[
                {
                    "id": "bash-1",
                    "index": 0,
                    "function": {"name": "bash", "arguments": command},
                }
            ],
        )

    return answer


async def _approval_requested(
    client: AppServerClient, incoming: AsyncIterator[object]
) -> str:
    """Take the next approval the client is asked for; return its callback ID."""
    while True:
        message = await asyncio.wait_for(anext(incoming), timeout=5)
        if isinstance(message, ServerRequest) and message.method == "callback/call":
            callback = CallbackCallParams.model_validate(message.params).callback
            await client.respond(
                message.id, CallbackCallResponse(callback_id=callback.callback_id)
            )
            return callback.callback_id


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_timeout", [SHORT_CALLBACK_TIMEOUT])
@pytest.mark.parametrize("forwarded", [TARGET_ANSWERS])
async def test_a_late_permanent_approval_of_a_timed_out_target_callback_saves_no_grant(
    target_session: TargetSession,
    target_model: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    callback_ids: dict[str, str],
    process_services: ProcessServices,
    config_dir: Path,
    tmp_path: Path,
) -> None:
    """The approval is still open in Vibe; the target already closed it.

    The Turn runs on Vibe's own Session so Vibe holds the open approval, and the
    bridge sends its answer to a target callback that timed out.
    """
    target_model.mock(side_effect=_model_answers(backend_contract_mistral_response))
    await target_session.start_turn("write it")
    target_callback_id = await target_session.open_callback_id()
    resolved = await target_session.wait_until_callback_resolved(target_callback_id)
    assert resolved["outcome"]["error"]["code"] == "callback_timed_out"
    config_file = config_dir / "config.toml"
    config_before = config_file.read_text(encoding="utf-8")
    client = await connect_backend_contract_client(
        session_options=SessionOptions(cwd=str(tmp_path), enabled_tools=["bash"]),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
        process_services=process_services,
    )
    incoming = client.incoming()
    try:
        session_id = await _start_vibe_session(client)
        await _start_turn(client, session_id)
        callback_id = await _approval_requested(client, incoming)
        callback_ids[callback_id] = target_callback_id

        error = await _refused(
            client.request(
                "callback/result",
                _approval(session_id, callback_id, "approve_permanently"),
            )
        )
    finally:
        await incoming.aclose()
        await client.close()

    assert error.code is ProtocolErrorCode.CALLBACK_CLOSED
    assert config_file.read_text(encoding="utf-8") == config_before


@pytest.mark.asyncio
@pytest.mark.parametrize("forwarded", [TARGET_ANSWERS])
async def test_a_permanent_approval_the_target_accepts_saves_the_grant(
    target_session: TargetSession,
    target_model: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    callback_ids: dict[str, str],
    process_services: ProcessServices,
    config_dir: Path,
    tmp_path: Path,
) -> None:
    """The approval is open in Vibe and in the target, which takes the answer.

    The grant is written after the target accepts it: the bash rule Vibe saves
    for a permanent approval, next to the defaults it writes with it.
    """
    target_model.mock(side_effect=_model_answers(backend_contract_mistral_response))
    await target_session.start_turn("write it")
    target_callback_id = await target_session.open_callback_id()
    config_file = config_dir / "config.toml"
    config_before = config_file.read_text(encoding="utf-8")
    client = await connect_backend_contract_client(
        session_options=SessionOptions(cwd=str(tmp_path), enabled_tools=["bash"]),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
        process_services=process_services,
    )
    incoming = client.incoming()
    try:
        session_id = await _start_vibe_session(client)
        await _start_turn(client, session_id)
        callback_id = await _approval_requested(client, incoming)
        callback_ids[callback_id] = target_callback_id

        await client.request(
            "callback/result", _approval(session_id, callback_id, "approve_permanently")
        )
    finally:
        await incoming.aclose()
        await client.close()

    config_after = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert "tools" not in tomllib.loads(config_before)
    assert "touch" not in BashToolConfig().allowlist
    assert sorted(config_after["tools"]["bash"]["allowlist"]) == sorted([
        *BashToolConfig().allowlist,
        "touch",
    ])
