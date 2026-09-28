from __future__ import annotations

from typing import Literal

import pytest
from textual.app import App, ComposeResult

from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicMessageSource,
    TextContentBlock,
)
from vibe.cli.textual_ui.widgets.messages import UserMessage, UserMessageSeverity
from vibe.cli.textual_ui.widgets.subagent_transcripts import (
    MAX_CACHED_SUBAGENT_TRANSCRIPTS,
    SubagentTranscripts,
)
from vibe.cli.textual_ui.windowing import HISTORY_RESUME_TAIL_MESSAGES


def _message(
    index: int,
    *,
    role: Literal["assistant", "user"] = "user",
    source: PublicMessageSource | None = None,
) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=f"message-{index}",
        session_id="child",
        turn_id="turn",
        created_at=index,
        updated_at=index,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role=role,
        source=source,
        content=[TextContentBlock(text=f"message {index}")],
    )


class _Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield SubagentTranscripts(id="subagent-transcripts")


@pytest.mark.asyncio
async def test_switching_to_a_cached_transcript_preserves_its_widget_tree() -> None:
    app = _Harness()

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        transcripts.select("child-1")
        await transcripts.replace_history(
            "child-1", [_message(1)], tools_collapsed=True, show_thinking=True
        )
        first_container = transcripts.transcript_container("child-1")
        assert first_container is not None
        first_message = first_container.query_one(UserMessage)

        transcripts.select("child-2")
        await transcripts.replace_history(
            "child-2", [_message(2)], tools_collapsed=True, show_thinking=True
        )
        transcripts.select("child-1")
        changed = await transcripts.replace_history(
            "child-1", [_message(1)], tools_collapsed=True, show_thinking=True
        )

        assert not changed
        assert transcripts.transcript_container("child-1") is first_container
        assert first_container.query_one(UserMessage) is first_message


@pytest.mark.asyncio
async def test_subagent_transcript_mounts_complete_history() -> None:
    app = _Harness()
    history: list[PublicHistoryEntry] = [
        _message(index) for index in range(HISTORY_RESUME_TAIL_MESSAGES + 10)
    ]

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        transcripts.select("child")
        await transcripts.replace_history(
            "child", history, tools_collapsed=True, show_thinking=True
        )

        container = transcripts.transcript_container("child")
        assert container is not None
        assert len(container.query(UserMessage)) == len(history)


@pytest.mark.asyncio
async def test_subagent_transcript_keeps_full_history_on_tail_refresh() -> None:
    app = _Harness()
    history: list[PublicHistoryEntry] = [
        _message(0, source="turn_start"),
        _message(1, role="assistant"),
        _message(2, source="turn_steer"),
        *[
            _message(index, role="assistant")
            for index in range(3, HISTORY_RESUME_TAIL_MESSAGES + 13)
        ],
    ]

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        transcripts.select("child")
        await transcripts.replace_history(
            "child",
            history,
            tools_collapsed=True,
            show_thinking=True,
            history_complete=True,
        )

        container = transcripts.transcript_container("child")
        assert container is not None
        assert len(container.children) == len(history)
        messages = list(container.query(UserMessage))
        assert [message.get_content() for message in messages] == [
            "message 0",
            "message 2",
        ]

        await transcripts.replace_history(
            "child",
            history[-HISTORY_RESUME_TAIL_MESSAGES:],
            tools_collapsed=True,
            show_thinking=True,
        )

        messages = list(container.query(UserMessage))
        assert len(container.children) == len(history)
        assert [message.get_content() for message in messages] == [
            "message 0",
            "message 2",
        ]


@pytest.mark.asyncio
async def test_subagent_transcript_keeps_parent_instruction_after_harness_cap() -> None:
    app = _Harness()
    history: list[PublicHistoryEntry] = [
        _message(0, source="turn_start"),
        *[_message(index, role="assistant") for index in range(1, 502)],
    ]

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        transcripts.select("child")
        await transcripts.replace_history(
            "child",
            history,
            tools_collapsed=True,
            show_thinking=True,
            history_complete=True,
        )

        await transcripts.replace_history(
            "child",
            history[-500:],
            tools_collapsed=True,
            show_thinking=True,
            history_complete=True,
        )

        container = transcripts.transcript_container("child")
        assert container is not None
        messages = list(container.query(UserMessage))
        assert [message.get_content() for message in messages] == ["message 0"]


@pytest.mark.asyncio
async def test_subagent_transcript_seeds_parent_instruction_before_harness_cap() -> (
    None
):
    app = _Harness()

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        transcripts.remember_parent_instruction(_message(0, source="turn_start"))
        transcripts.select("child")
        await transcripts.replace_history(
            "child",
            [_message(index, role="assistant") for index in range(1, 501)],
            tools_collapsed=True,
            show_thinking=True,
            history_complete=True,
        )

        container = transcripts.transcript_container("child")
        assert container is not None
        messages = list(container.query(UserMessage))
        assert [message.get_content() for message in messages] == ["message 0"]


@pytest.mark.asyncio
async def test_subagent_transcript_keeps_parent_instruction_after_lru_eviction() -> (
    None
):
    app = _Harness()

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        transcripts.select("child")
        await transcripts.replace_history(
            "child",
            [_message(0, source="turn_start")],
            tools_collapsed=True,
            show_thinking=True,
            history_complete=True,
        )

        for index in range(MAX_CACHED_SUBAGENT_TRANSCRIPTS + 1):
            session_id = f"other-{index}"
            transcripts.select(session_id)
            await transcripts.replace_history(
                session_id,
                [_message(index + 1, role="assistant")],
                tools_collapsed=True,
                show_thinking=True,
                history_complete=True,
            )

        assert transcripts.transcript_container("child") is None

        transcripts.select("child")
        await transcripts.replace_history(
            "child",
            [_message(1000, role="assistant")],
            tools_collapsed=True,
            show_thinking=True,
            history_complete=True,
        )

        container = transcripts.transcript_container("child")
        assert container is not None
        messages = list(container.query(UserMessage))
        assert [message.get_content() for message in messages] == ["message 0"]


@pytest.mark.asyncio
async def test_local_styled_user_message_survives_history_refresh() -> None:
    app = _Harness()

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        transcripts.select("child")
        await transcripts.replace_history(
            "child", [_message(1)], tools_collapsed=True, show_thinking=True
        )
        await transcripts.append_local_user_message(
            "child", "Local only", severity=UserMessageSeverity.ERROR
        )
        await transcripts.replace_history(
            "child",
            [_message(1), _message(2)],
            tools_collapsed=True,
            show_thinking=True,
        )

        container = transcripts.transcript_container("child")
        assert container is not None
        messages = list(container.query(UserMessage))
        assert [message.get_content() for message in messages] == [
            "message 1",
            "message 2",
            "Local only",
        ]
        assert messages[-1].history_entry_id is None
        assert messages[-1].severity is UserMessageSeverity.ERROR


@pytest.mark.asyncio
async def test_subagent_transcript_cache_evicts_the_least_recent_inactive_view() -> (
    None
):
    app = _Harness()

    async with app.run_test():
        transcripts = app.query_one(SubagentTranscripts)
        for index in range(MAX_CACHED_SUBAGENT_TRANSCRIPTS + 1):
            session_id = f"child-{index}"
            transcripts.select(session_id)
            await transcripts.prepare(session_id)

        assert transcripts.transcript_container("child-0") is None
        assert (
            transcripts.transcript_container(f"child-{MAX_CACHED_SUBAGENT_TRANSCRIPTS}")
            is not None
        )
