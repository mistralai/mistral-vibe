"""The final answer the app server reads from a real target-runtime Session."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from mistralai.vibe.harness.app_server.session import (
    AgentConfig,
    HookDefinition,
    SessionClient,
    SessionStartParams,
    models as target_models,
    procedures as target_procedures,
)
from mistralai.vibe.harness.app_server.session.local import LocalSessionTransport
from mistralai.vibe.harness.app_server.session.local_manager import (
    LocalSessionBinding,
    LocalSessionManager,
    LocalSessionWorkspace,
)
from mistralai.vibe.harness.core import protocol as core_protocol
from mistralai.vibe.harness.runtime import (
    CallableCompletionProvider,
    CompletionUsage,
    ModelAssistantContentDelta,
    ModelCompletionFinished,
)
from mistralai.vibe.harness.runtime.capabilities import (
    CapabilityBindingReference,
    CapabilityInvocation,
    CapabilityInvocationResult,
    PostAgentTurnHookAccept,
    PostAgentTurnHookRetry,
)
from mistralai.vibe.harness.runtime.capabilities.registrations import (
    CallableRegistration,
    CapabilityRegistrationRegistry,
)
from mistralai.vibe.harness.runtime.completion.models import (
    CompletionRequest,
    CompletionStreamItem,
)
from mistralai.vibe.harness.runtime.session.definition import SessionDefinition
import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    SessionEventAdapter,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    SessionReadResponse,
)
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    VibeSessionReadResponse,
)
from tests.app_server._session_api_harness import StreamBatches, api_adapter, publish
from tests.app_server.target_session_bridge import TARGET_MODEL, target_model_facts
from tests.constants import MISTRAL_BASE_URL
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_api_session import ApiSessionState
from vibe.app_server.events import TurnCompleted
from vibe.app_server.models import PublicHistoryEntry, PublicMessageEntry

_REVIEW_HOOK = HookDefinition(type="post_agent_turn", name="review")


class _ReviewOnce:
    """A ``post_agent_turn`` hook that sends the first answer back, then accepts."""

    def __init__(self) -> None:
        self.reviews = 0

    async def __call__(
        self, _invocation: CapabilityInvocation
    ) -> CapabilityInvocationResult:
        self.reviews += 1
        if self.reviews == 1:
            return PostAgentTurnHookRetry(
                feedback=[core_protocol.TextContentBlock(text="Shorter, please.")]
            )
        return PostAgentTurnHookAccept()


@asynccontextmanager
async def _target_session_with_review() -> AsyncIterator[tuple[SessionClient, str]]:
    """A target Session whose model answers twice and whose hook retries once."""
    answers = iter(["A long first draft.", "The final answer."])

    async def complete(
        _request: CompletionRequest,
    ) -> AsyncIterator[CompletionStreamItem]:
        yield ModelAssistantContentDelta(
            content=[core_protocol.TextContentBlock(text=next(answers))]
        )
        yield ModelCompletionFinished(finish_reason="stop", usage=CompletionUsage())

    review = _ReviewOnce()
    registrations = CapabilityRegistrationRegistry.from_registrations([
        CallableRegistration(
            reference=CapabilityBindingReference(id=_REVIEW_HOOK.binding_reference.id),
            callable=review,
        )
    ])

    async def bind(
        definition: SessionDefinition, _root: LocalSessionWorkspace | None
    ) -> LocalSessionBinding:
        return LocalSessionBinding(definition=definition, registrations=registrations)

    manager = LocalSessionManager(
        completion_provider_factory=lambda _: CallableCompletionProvider(complete),
        binding_factory=bind,
    )
    client = SessionClient(LocalSessionTransport.attach(manager))
    config = AgentConfig(
        completion={
            "type": "mistral",
            "model": TARGET_MODEL,
            "base_url": MISTRAL_BASE_URL,
        },
        hooks=[_REVIEW_HOOK],
    )
    try:
        with target_model_facts():
            await client.start_session(SessionStartParams(agent_config=config))
            yield client, client.session_id
    finally:
        await client.close()
        await manager.close()
    assert review.reviews == 2


async def _run_reviewed_turn(client: SessionClient) -> list[dict[str, object]]:
    """Run one Turn; return the payloads of the events it published, in order."""
    events = client.events()
    await client.start_turn([target_models.TextContentBlock(text="Explain it")])
    payloads: list[dict[str, object]] = []
    async for event in events:
        payload = event.payload.model_dump(mode="json", by_alias=True)
        payloads.append(payload)
        if isinstance(event.payload, target_models.TurnTerminatedEvent):
            break
    await events.aclose()
    return payloads


@pytest.mark.asyncio
async def test_turn_completed_carries_the_answer_the_hook_accepted(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A target-runtime Session whose ``post_agent_turn`` hook sends
        the model's first answer back for a retry, then accepts the second.
    *Do*: Run a Turn there, and publish the events it produced to a real
        adapter on the Session API path, as the stream would bring them.
    *Assert*: The adapter's ``turn/completed`` carries the accepted answer,
        not the draft, though the history shows both.
    """
    # Prepare
    async with _target_session_with_review() as (client, _session_id):
        payloads = await _run_reviewed_turn(client)

    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        turn_events = [
            SessionEventAdapter.validate_python(payload)
            for payload in payloads
            if payload["type"]
            in {
                "turn_started",
                "history_entry_added",
                "history_entry_completed",
                "turn_terminated",
            }
        ]

        # Do
        events = await publish(
            adapter, StreamBatches(adapter.session_id).batch(*turn_events)
        )
        state = adapter._translated_state
        assert state is not None

    # Assert
    [completed] = [e.event.turn for e in events if isinstance(e.event, TurnCompleted)]
    assert completed.final_answer == "The final answer."
    assert _assistant_texts(state.history or []) == [
        "A long first draft.",
        "The final answer.",
    ]


def _assistant_texts(history: list[PublicHistoryEntry]) -> list[str]:
    return [
        entry.text
        for entry in history
        if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
    ]


@pytest.mark.asyncio
async def test_a_draft_a_hook_sent_back_is_never_the_final_answer(
    tmp_path: Path,
) -> None:
    """*Prepare*: A target-runtime Session whose ``post_agent_turn`` hook sends
        the model's first answer back for a retry, then accepts the second.
    *Do*: Run a Turn, then read the Session as the app server does, through the
        target's own ``session/read``.
    *Assert*: The history holds both answers, the draft with nothing marking it
        discarded, but the Turn's final answer is the second one.
    """
    async with _target_session_with_review() as (client, session_id):
        # Prepare
        await _run_reviewed_turn(client)

        # Do
        target_read = await client.request(
            "app_server/session/read",
            target_procedures.SessionReadParams(
                session_id=session_id,
                history=target_models.PageRequest(limit=100),
                turns=target_models.PageRequest(limit=1, direction="backward"),
            ),
            target_procedures.SessionReadResponse,
        )
    read = SessionReadResponse.model_validate(
        target_read.model_dump(mode="json", by_alias=True)
    )
    state = ApiSessionState(session_id, (tmp_path,))
    state.reset(
        read,
        VibeSessionReadResponse(
            session_id=session_id, last_event_id=read.last_event_id, extensions={}
        ),
    )

    # Assert
    assert _assistant_texts(state.history) == [
        "A long first draft.",
        "The final answer.",
    ]
    turns = state.runtime_state().latest_turn
    assert turns is not None
    assert state.final_answer_of(turns.id) == "The final answer."
