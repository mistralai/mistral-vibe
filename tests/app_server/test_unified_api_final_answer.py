"""The final answer the adapter gives a completed Turn on the Session API path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    CompletedTermination,
    TurnStartedEvent,
    TurnState,
    TurnTerminatedEvent,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    SessionTurnsListResponse as ApiTurnsListResponse,
)
from tests.app_server._session_api_harness import (
    StreamBatches,
    api_adapter,
    api_transport,
    forwarding,
    publish,
    until,
    use_answering_model,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import TurnCompleted
from vibe.app_server.models import PublicTurn, TextContentBlock
from vibe.app_server.protocol import (
    SessionTurnsListParams,
    SessionTurnsListResponse,
    TurnStartParams,
)

_ANSWER: JsonValue = [
    {"type": "text", "text": "The final answer."},
    {"type": "image", "data": "aGk=", "mimeType": "image/png"},
]
"""An answer as the target runtime reports it: the accepted reply's content."""


def _terminated(output: JsonValue, stop_reason: str | None = None) -> TurnState:
    return TurnState(
        id="turn-1",
        status="terminated",
        started_at=10,
        completed_at=13,
        termination=CompletedTermination.model_validate({
            "output": output,
            "stopReason": stop_reason,
        }),
    )


async def _completed_turns(
    adapter: Any, batches: StreamBatches, turn: TurnState
) -> list[PublicTurn]:
    await publish(
        adapter,
        batches.batch(
            TurnStartedEvent(
                turn=TurnState(id="turn-1", status="running", started_at=10)
            )
        ),
    )
    events = await publish(adapter, batches.batch(TurnTerminatedEvent(turn=turn)))
    return [e.event.turn for e in events if isinstance(e.event, TurnCompleted)]


@pytest.mark.asyncio
async def test_a_completed_turn_carries_the_runtime_answer_in_turn_completed(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path, and a started Turn.
    *Do*: Publish the Turn's end with the answer the runtime accepted.
    *Assert*: ``turn/completed`` and the state's Turn carry the answer's text.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter

        # Do
        completed = await _completed_turns(
            adapter, StreamBatches(adapter.session_id), _terminated(_ANSWER)
        )
        state = adapter._translated_state
        assert state is not None

    # Assert
    assert [turn.final_answer for turn in completed] == ["The final answer."]
    assert [turn.final_answer for turn in state.turns or []] == ["The final answer."]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "stop_reason", "skip_reason"),
    [
        (
            [{"type": "text", "text": "Deploys are frozen"}],
            "skipped",
            "Deploys are frozen",
        ),
        ("Deploys are frozen", "skipped", "Deploys are frozen"),
        ([{"type": "image", "data": "aGk=", "mimeType": "image/png"}], None, None),
    ],
)
async def test_a_turn_without_an_answer_of_its_own_carries_none(
    tmp_path: Path,
    process_services: ProcessServices,
    output: JsonValue,
    stop_reason: str | None,
    skip_reason: str | None,
) -> None:
    """*Prepare*: A real adapter on the Session API path, and a started Turn.
    *Do*: Publish the Turn's end skipped by a hook, with its reason as the target
        (blocks) or the local runtime (a string) reports it, or with no text.
    *Assert*: ``turn/completed`` carries no final answer, so a client falls back
        to the history; a skipped Turn keeps its reason as ``skipReason``.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter

        # Do
        [completed] = await _completed_turns(
            adapter, StreamBatches(adapter.session_id), _terminated(output, stop_reason)
        )

    # Assert
    assert completed.final_answer is None
    assert completed.skip_reason == skip_reason
    assert "finalAnswer" not in completed.model_dump(mode="json", by_alias=True)


@pytest.mark.asyncio
async def test_listed_turns_carry_the_runtime_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A session on the Session API path with one answered Turn, and
        a ``turns/list`` that reports its end as the target runtime does.
    *Do*: List the Turns.
    *Assert*: The listed Turn carries the answer's text.
    """
    # Prepare
    use_answering_model(monkeypatch)
    async with (
        api_adapter(tmp_path, process_services=process_services) as harness,
        forwarding(harness) as forwarded,
    ):
        adapter = harness.adapter
        session_id = adapter.session_id
        await adapter.start_turn(
            TurnStartParams(
                session_id=session_id, message=[TextContentBlock(text="ask")]
            )
        )
        await until(lambda: any(isinstance(e.event, TurnCompleted) for e in forwarded))
        await adapter.flush_events()
        transport = api_transport(adapter)
        request = transport.request

        async def as_the_target(method: str, *args: Any, **kwargs: Any) -> Any:
            response = await request(method, *args, **kwargs)
            if method != "app_server/session/turns/list":
                return response
            assert isinstance(response, ApiTurnsListResponse)
            items = [
                turn.model_copy(
                    update={"termination": _terminated(_ANSWER).termination}
                )
                for turn in response.page.items
            ]
            page = response.page.model_copy(update={"items": items})
            return response.model_copy(update={"page": page})

        monkeypatch.setattr(transport, "request", as_the_target)

        # Do
        turns = SessionTurnsListResponse.model_validate(
            (
                await adapter.dispatch_extension(
                    "session/turns/list",
                    SessionTurnsListParams(session_id=session_id).model_dump(
                        mode="json", by_alias=True
                    ),
                )
            ).response.model_dump(mode="json", by_alias=True)
        )

    # Assert
    assert [turn.final_answer for turn in turns.items] == ["The final answer."]
