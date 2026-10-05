from __future__ import annotations

import pytest

from vibe.app_server._loop_prompt import (
    LOOP_INSTRUCTIONS,
    is_loop_prompt,
    prepare_loop_queue,
    prepare_loop_turn,
    project_loop_message,
    project_loop_queue,
)
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    PublicQueuedTurn,
    PublicTurnQueue,
    SessionTextContentBlock,
    TextContentBlock,
    TurnContextInputEntry,
    TurnUserInputEntry,
)
from vibe.app_server.protocol import TurnEnqueueParams, TurnStartParams


@pytest.mark.parametrize(
    "text", ["/loop", "/LOOP whenever needed", "/loop\nnightly check CI"]
)
def test_loop_command_accepts_free_form_arguments(text: str) -> None:
    assert is_loop_prompt(text)


@pytest.mark.parametrize("text", ["", "/loopy check CI", "explain /loop", "/looping"])
def test_loop_command_does_not_match_other_prompts(text: str) -> None:
    params = TurnStartParams(session_id="s1", message=[TextContentBlock(text=text)])
    assert prepare_loop_turn(params) is params


def test_instruction_block_is_model_visible_but_not_in_public_history() -> None:
    params = TurnStartParams(
        session_id="s1",
        message=[TextContentBlock(text="/loop   every 90 seconds\ncheck CI")],
    )
    prepared = prepare_loop_turn(params)
    assert prepared.message[0] == params.message[0]
    assert prepared.message[-1] == TextContentBlock(text=LOOP_INSTRUCTIONS)
    entry = PublicMessageEntry(
        id="m1",
        session_id="s1",
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=prepared.message,
        source="turn_start",
    )
    projected = project_loop_message(entry)
    assert isinstance(projected, PublicMessageEntry)
    assert projected.content == params.message
    assert entry.content == prepared.message


def test_queue_projection_hides_instructions_without_changing_canonical_input() -> None:
    params = TurnEnqueueParams(
        session_id="s1",
        entries=[
            TurnContextInputEntry(
                content=[SessionTextContentBlock(text="keep this context")]
            ),
            TurnUserInputEntry(
                content=[SessionTextContentBlock(text="/loop weekdays at 9am check CI")]
            ),
        ],
    )
    prepared = prepare_loop_queue(params)
    assert prepared.entries[0] == params.entries[0]
    assert prepared.entries[-1].content[-1] == SessionTextContentBlock(
        text=LOOP_INSTRUCTIONS
    )
    queue = PublicTurnQueue(
        items=[PublicQueuedTurn(id="q1", created_at=0, entries=prepared.entries)]
    )
    projected = project_loop_queue(queue)
    assert projected.items[0].entries == params.entries
    assert queue.items[0].entries == prepared.entries


def test_instruction_text_in_an_ordinary_message_is_not_hidden() -> None:
    entry = PublicMessageEntry(
        id="m1",
        session_id="s1",
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=LOOP_INSTRUCTIONS)],
        source="turn_start",
    )
    assert project_loop_message(entry) == entry
