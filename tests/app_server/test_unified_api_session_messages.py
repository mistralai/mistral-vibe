"""Messages from another session, on the Session API path, as Desktop's
sessions run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
)
from mistralai_vibe_local_harness.vibe import _local_actions
from tests.app_server._session_api_harness import (
    USAGE,
    api_adapter,
    forwarding,
    until,
    use_answering_model,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import TurnCompleted, TurnStarted
from vibe.app_server.models import (
    PublicMessageEntry,
    SessionTextContentBlock,
    TextContentBlock,
    TurnUserInputEntry,
)
from vibe.app_server.protocol import (
    PeerDelivery,
    SessionMessageSender,
    SessionReadParams,
    TurnEnqueueParams,
)


def _from_session(
    session_id: str, text: str, *, delivery: PeerDelivery = "start_turn"
) -> TurnEnqueueParams:
    return TurnEnqueueParams(
        session_id=session_id,
        entries=[TurnUserInputEntry(content=[SessionTextContentBlock(text=text)])],
        sender=SessionMessageSender(session_id="peer", title="CI watcher"),
        delivery=delivery,
    )


@pytest.mark.asyncio
async def test_a_turn_the_user_queues_resets_the_hops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: An idle session another session's message ran a Turn in.
    *Do*: The user queues a message, which starts the next Turn.
    *Assert*: The message's Turn counts its hop; the user's Turn resets it.
    """
    use_answering_model(monkeypatch)
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        backend = harness.adapter
        session_id = backend.session_id

        async def enqueue(params: TurnEnqueueParams) -> None:
            result = await backend.enqueue_turn(params)
            if result.after_response is not None:
                result.after_response()

        async with forwarding(harness) as forwarded:

            def completed() -> int:
                return sum(isinstance(e.event, TurnCompleted) for e in forwarded)

            # Prepare
            await enqueue(_from_session(session_id, "Start two sessions"))
            await until(lambda: completed() == 1)
            after_message = harness.host.message_hops(session_id)

            # Do
            await enqueue(
                TurnEnqueueParams(
                    session_id=session_id,
                    entries=[
                        TurnUserInputEntry(
                            content=[SessionTextContentBlock(text="please start them")]
                        )
                    ],
                )
            )
            await until(lambda: completed() == 2)

        # Assert
        assert (after_message, harness.host.message_hops(session_id)) == (1, 0)


@pytest.mark.asyncio
async def test_a_queued_message_waits_for_the_next_turn_of_an_idle_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: An idle session.
    *Do*: Another session sends it an update with `delivery: "queue"`, then
    the user writes.
    *Assert*: The update starts no turn, shows in history as a message from
    that session, and the model reads it in the user's turn.
    """
    seen: list[str] = []

    async def execute_completion(
        action: Any, messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        seen.append(repr(messages))
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=[RustTextContentBlock(text="answer")],
                finish_reason="stop",
                usage=USAGE,
            ),
        )

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        backend = harness.adapter
        session_id = backend.session_id
        async with forwarding(harness) as forwarded:

            def turns(kind: type) -> int:
                return sum(isinstance(e.event, kind) for e in forwarded)

            # Do
            queued = await backend.enqueue_turn(
                _from_session(session_id, "FYI: CI is green", delivery="queue")
            )
            if queued.after_response is not None:
                queued.after_response()
            started_by_update = turns(TurnStarted)
            history = (
                await backend.read(SessionReadParams(session_id=session_id))
            ).state.history or []
            user = await backend.enqueue_turn(
                TurnEnqueueParams(
                    session_id=session_id,
                    entries=[
                        TurnUserInputEntry(
                            content=[SessionTextContentBlock(text="What's new?")]
                        )
                    ],
                )
            )
            if user.after_response is not None:
                user.after_response()
            await until(lambda: turns(TurnCompleted) == 1)

    # Assert
    assert queued.response.waits
    assert started_by_update == 0
    shown = [entry for entry in history if isinstance(entry, PublicMessageEntry)]
    assert shown[-1].content == [TextContentBlock(text="FYI: CI is green")]
    assert shown[-1].user_display_content is not None
    assert shown[-1].user_display_content.content == [
        {"type": "vibe.session_message", "sessionId": "peer", "title": "CI watcher"}
    ]
    assert len(seen) == 1
    assert "FYI: CI is green" in seen[0]
    assert "What's new?" in seen[0]
