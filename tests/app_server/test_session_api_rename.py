"""``catalog/rename`` from the app server, against the target runtime's local host."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mistralai.vibe.harness.app_server.session import (
    AgentConfig,
    SessionClient,
    SessionStartParams,
    models as target_models,
    procedures as target_procedures,
)
from mistralai.vibe.harness.app_server.session.local import LocalSessionTransport
from mistralai.vibe.harness.app_server.session.local_manager import LocalSessionManager
from mistralai.vibe.harness.app_server.session.transport import (
    SessionTransport as TargetTransport,
)
from mistralai.vibe.harness.runtime.completion.models import (
    CompletionRequest,
    CompletionStreamItem,
)
from mistralai.vibe.harness.runtime.completion.provider import (
    CallableCompletionProvider,
)
import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models.base import (
    SessionModel,
)
from mistralai_vibe_local_harness.session_api.app_server.session.models.events import (
    ClientTransportStreamItem,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    CallbacksTreeParams,
    CallbacksTreeResponse,
    EventsReadParams,
)
from vibe.app_server._session_api_commands import rename_session


class _TargetModelsBridge:
    """Sends the app server's Session API models to a target runtime transport.

    Test-only. The app server builds requests from the local runtime's copy of
    the Session API models, and the target runtime's host checks requests
    against its own classes. The copy is byte for byte, so each model is
    re-validated as the class of the same name on the other side. The
    production bridge belongs to step 3 of the migration (local and target
    Sessions side by side), which needs one for every command.
    """

    def __init__(self, target: TargetTransport) -> None:
        self._target = target
        self.requests: list[str] = []

    async def request[ResultT: SessionModel](
        self, method: str, params: SessionModel, result_type: type[ResultT]
    ) -> ResultT:
        self.requests.append(method)
        target_params = getattr(target_procedures, type(params).__name__)
        target_result = getattr(target_procedures, result_type.__name__)
        result = await self._target.request(
            method,
            target_params.model_validate(params.model_dump(by_alias=True)),
            target_result,
        )
        return result_type.model_validate(result.model_dump(by_alias=True))

    def read_events(
        self, params: EventsReadParams
    ) -> AsyncIterator[ClientTransportStreamItem]:
        raise NotImplementedError

    def watch_callbacks_tree(
        self, params: CallbacksTreeParams
    ) -> AsyncIterator[CallbacksTreeResponse]:
        raise NotImplementedError

    async def close(self) -> None:
        await self._target.close()


async def _no_completion(
    _request: CompletionRequest,
) -> AsyncIterator[CompletionStreamItem]:
    raise AssertionError("renaming a Session runs no model")
    yield


@asynccontextmanager
async def _target_session() -> AsyncIterator[tuple[SessionClient, _TargetModelsBridge]]:
    """A Session started on the target runtime's local host, and a bridge to it."""
    manager = LocalSessionManager(
        completion_provider_factory=lambda _: CallableCompletionProvider(_no_completion)
    )
    client = SessionClient(LocalSessionTransport.attach(manager))
    bridge = _TargetModelsBridge(LocalSessionTransport.attach(manager))
    try:
        await client.start_session(SessionStartParams(agent_config=AgentConfig()))
        yield client, bridge
    finally:
        await bridge.close()
        await client.close()
        await manager.close()


@pytest.mark.asyncio
async def test_rename_session_titles_a_target_session() -> None:
    """*Prepare*: A Session on the target runtime's local host.
    *Do*: Rename it with surrounding spaces through ``rename_session``.
    *Assert*: The reply and a fresh read of the Session carry the stripped
        title.
    """
    async with _target_session() as (client, bridge):
        # Prepare
        session_id = client.session_id

        # Do
        renamed = await rename_session(bridge, session_id, "  Billing retries  ")
        read = await client.read_session()

        # Assert
        assert renamed.id == session_id
        assert renamed.title == "Billing retries"
        assert read.state.session.title == "Billing retries"


@pytest.mark.asyncio
async def test_rename_session_publishes_the_new_title() -> None:
    """*Prepare*: A Session on the target runtime's local host.
    *Do*: Rename it through ``rename_session``, then read its next event.
    *Assert*: The host publishes a ``session_updated`` event with the title, the
        event a client follows to show it.
    """
    async with _target_session() as (client, bridge):
        # Prepare
        events = client.events()

        # Do
        await rename_session(bridge, client.session_id, "Billing retries")
        event = await anext(events)
        await events.aclose()

        # Assert
        assert isinstance(event.payload, target_models.SessionUpdatedEvent)
        assert event.payload.session.title == "Billing retries"


@pytest.mark.asyncio
async def test_rename_session_refuses_an_empty_title_before_any_request() -> None:
    """*Prepare*: A Session on the target runtime's local host.
    *Do*: Rename it with a blank title.
    *Assert*: The rename is refused and the bridge sent no request, so the
        Session keeps no title.
    """
    async with _target_session() as (client, bridge):
        # Prepare
        sent = bridge.requests

        # Do
        with pytest.raises(ValueError, match="cannot be empty"):
            await rename_session(bridge, client.session_id, "   ")

        # Assert
        assert sent == []
        read = await client.read_session()
        assert read.state.session.title is None
