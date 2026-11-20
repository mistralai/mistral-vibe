from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
import tomllib

import httpx
import pytest
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
)
from tests.stubs.process_services import fake_process_services
from vibe.app_server import (
    _credential_selection as credential_selection,
    _unified_harness_backend_adapter as adapter_module,
)
from vibe.app_server._keep_awake import KeepAwake, Release
from vibe.app_server.events import CallbackRequested
from vibe.app_server.models import (
    UserAnswer,
    UserInputCallbackOutput,
    UserQuestionResult,
)
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    ConfigWriteOpWire,
    ConfigWriteParams,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession
from vibe.core.platform_auth import ApiKeyPlatformCredentialProvider

pytestmark = pytest.mark.both_paths

# Short polls, so a test waits a few milliseconds for what a user waits seconds.
_POLL_S = 0.01


@pytest.fixture(autouse=True)
def _fast_keep_awake_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "KEEP_AWAKE_POLL_S", _POLL_S)
    monkeypatch.setattr(adapter_module, "KEEP_AWAKE_IDLE_POLL_S", _POLL_S * 2)


class _CountingKeepAwake(KeepAwake):
    """Counts the updates the sessions' polls send, one per poll."""

    def __init__(self, acquire: Callable[[], Release]) -> None:
        super().__init__(acquire=acquire)
        self.updates = 0

    def update(self, holder: object, *, active: bool) -> None:
        self.updates += 1
        super().update(holder, active=active)


class _Request:
    """The OS request, recorded instead of asked for."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.keep_awake = _CountingKeepAwake(acquire=self.acquire)

    def acquire(self) -> Release:
        self.calls.append("acquire")
        return lambda: self.calls.append("release")

    def held(self) -> bool:
        assert self.keep_awake.settle(timeout=5)
        return self.calls[-1:] == ["acquire"]

    async def becomes(self, held: bool) -> bool:
        """Whether the request reaches ``held`` within a few polls of the session."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        while self.held() != held:
            if loop.time() > deadline:
                return False
            await asyncio.sleep(_POLL_S)
        return True

    async def after_polls(self, polls: int = 5) -> bool:
        """Whether the request is held once the session has polled ``polls`` more
        times, so a check that nothing was asked for is made after real polls.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5
        target = self.keep_awake.updates + polls
        while self.keep_awake.updates < target:
            assert loop.time() < deadline, "the session stopped polling"
            await asyncio.sleep(_POLL_S)
        return self.held()


def _question(build_response: Callable[..., httpx.Response]) -> httpx.Response:
    return build_response(
        "",
        tool_calls=[
            {
                "id": "question-1",
                "index": 0,
                "function": {
                    "name": "ask_user_question",
                    "arguments": (
                        '{"questions":[{"question":"Ship it?",'
                        '"options":[{"label":"Yes"},{"label":"No"}]}]}'
                    ),
                },
            }
        ],
    )


_ANSWER = UserInputCallbackOutput(
    result=UserQuestionResult(answers=[UserAnswer(question="Ship it?", answer="Yes")])
)

type WhileWaiting = Callable[
    [BackendContractConnection, AppServerSession, str], Awaitable[bool]
]


async def _answer(
    _connection: BackendContractConnection, session: AppServerSession, callback_id: str
) -> bool:
    await session.respond_to_callback(callback_id, _ANSWER)
    return True


async def _run_question_turn(
    request: _Request,
    mistral_api: respx.Route,
    build_response: Callable[..., httpx.Response],
    while_waiting: WhileWaiting,
    client_info: ClientInfo | None = None,
    *,
    expect_held: bool = True,
) -> tuple[list[bool], bool]:
    """Whether the request was held while the question waited, and after the turn.

    ``while_waiting`` runs once the question opened; it answers and returns True,
    or closes the session and returns False.
    """
    mistral_api.mock(side_effect=[_question(build_response), build_response("Done")])
    services = fake_process_services(keep_awake=request.keep_awake)
    held_while_waiting: list[bool] = []
    try:
        connection = await connect_backend_contract_host(
            session_options=SessionOptions(enabled_tools=["ask_user_question"]),
            capabilities=ClientCapabilities(callback_kinds=["user_input"]),
            process_services=services,
            client_info=client_info,
        )
        try:
            session = await connection.host.open_session()
            try:
                async for event in session.act("ask me"):
                    if isinstance(event, CallbackRequested):
                        held_while_waiting.append(
                            await request.becomes(True)
                            if expect_held
                            else await request.after_polls()
                        )
                        answered = await while_waiting(
                            connection, session, event.callback.callback_id
                        )
                        if not answered:
                            break
            finally:
                await session.close()
            held_after = request.held()
        finally:
            await connection.host.close()
    finally:
        await services.close()
    return held_while_waiting, held_after


@pytest.mark.asyncio
async def test_a_turn_keeps_the_machine_awake_until_it_ends(
    monkeypatch: pytest.MonkeyPatch,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    """*Prepare*: ``keep_awake`` on, and a model that asks a question, then answers.
    *Do*: Run one turn, answering the question.
    *Assert*: The request is held while the question waits and released once the
    turn ends, once.
    """
    # Prepare
    monkeypatch.setenv("VIBE_KEEP_AWAKE", "true")
    request = _Request()

    # Do
    held_while_waiting, held_after = await _run_question_turn(
        request,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
        _answer,
    )

    # Assert
    assert held_while_waiting == [True]
    assert not held_after
    assert request.calls == ["acquire", "release"]


@pytest.mark.asyncio
async def test_turning_the_setting_off_mid_turn_lets_the_machine_sleep(
    config_dir: Path,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    """*Prepare*: ``keep_awake`` on, and a turn waiting on a question.
    *Do*: Turn the setting off for the session, then answer.
    *Assert*: The request is released at the next poll, before the answer.
    """
    # Prepare
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["keep_awake"] = True
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    request = _Request()
    released_on_write: list[bool] = []

    async def turn_off(
        connection: BackendContractConnection,
        session: AppServerSession,
        callback_id: str,
    ) -> bool:
        await connection.client.request(
            "config/write",
            ConfigWriteParams(
                session_id=session.session_id,
                ops=[
                    ConfigWriteOpWire(
                        op="set",
                        path="/keep_awake",
                        value=False,
                        target_layer="overrides",
                    )
                ],
            ),
        )
        released_on_write.append(await request.becomes(False))
        return await _answer(connection, session, callback_id)

    # Do
    held_while_waiting, held_after = await _run_question_turn(
        request,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
        turn_off,
    )

    # Assert
    assert held_while_waiting == [True]
    assert released_on_write == [True]
    assert not held_after


@pytest.mark.asyncio
async def test_closing_a_session_mid_turn_lets_the_machine_sleep(
    monkeypatch: pytest.MonkeyPatch,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    """*Prepare*: ``keep_awake`` on, and a turn waiting on a question.
    *Do*: Close the session without answering.
    *Assert*: The request is released.
    """
    # Prepare
    monkeypatch.setenv("VIBE_KEEP_AWAKE", "true")
    request = _Request()

    async def close(
        _connection: BackendContractConnection,
        session: AppServerSession,
        _callback_id: str,
    ) -> bool:
        await session.close()
        return False

    # Do
    held_while_waiting, held_after = await _run_question_turn(
        request, backend_contract_mistral_api, backend_contract_mistral_response, close
    )

    # Assert
    assert held_while_waiting == [True]
    assert not held_after
    assert request.calls == ["acquire", "release"]


@pytest.mark.asyncio
async def test_a_turn_never_asks_when_the_setting_is_off(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    """*Prepare*: ``keep_awake`` left at its default.
    *Do*: Run one turn that asks a question.
    *Assert*: The machine is never asked to stay awake.
    """
    # Prepare
    request = _Request()

    # Do
    held_while_waiting, _ = await _run_question_turn(
        request,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
        _answer,
        expect_held=False,
    )

    # Assert
    assert held_while_waiting == [False]
    assert request.calls == []


class _SignedIn(ApiKeyPlatformCredentialProvider):
    def __init__(self) -> None:
        super().__init__("MISTRAL_API_KEY")


@pytest.mark.asyncio
async def test_a_desktop_session_leaves_keeping_awake_to_desktop(
    monkeypatch: pytest.MonkeyPatch,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
) -> None:
    """*Prepare*: ``keep_awake`` on, and a client that says it is Vibe Desktop.
    *Do*: Run one turn that asks a question.
    *Assert*: The app server never asks: Desktop keeps the machine awake under its
    own setting.
    """
    # Prepare
    monkeypatch.setenv("VIBE_KEEP_AWAKE", "true")
    # Stands in for Desktop's sign-in, which this test does not exercise: a
    # Desktop connection without one gets no credential at all. The runtime's
    # own check keeps the real class, which this key passes as signed in.
    monkeypatch.setattr(credential_selection, "DesktopCredentialUnavailable", _SignedIn)
    request = _Request()

    # Do
    held_while_waiting, _ = await _run_question_turn(
        request,
        backend_contract_mistral_api,
        backend_contract_mistral_response,
        _answer,
        client_info=ClientInfo(name="desktop", version="0", entrypoint="desktop"),
        expect_held=False,
    )

    # Assert
    assert held_while_waiting == [False]
    assert request.calls == []
