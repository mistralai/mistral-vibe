"""Session titles: when a title call is due, what the title model reads, and the
title that comes back.
"""

from __future__ import annotations

import asyncio
from itertools import count

from pydantic import JsonValue
import pytest

from vibe.app_server._session_titles import (
    TitleCadence,
    TitlePolicy,
    TitleRequest,
    build_title_transcript,
    clean_title,
    generate_session_title,
)
from vibe.app_server.models import (
    CommittedGenerationOutcome,
    CompletedEffectState,
    DiscardedGenerationOutcome,
    EffectCallDisplay,
    EffectResultDisplay,
    EffectState,
    FailedEffectState,
    GenericEffectDetail,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    RunningEffectState,
    SessionTitleUpdatedNoticeDetail,
    ShellEffectDetail,
    ShellEffectInput,
    TextContentBlock,
)

_IDS = count()
_COMPLETED = PublicEntryGenerationStatus.COMPLETED
_IN_PROGRESS = PublicEntryGenerationStatus.IN_PROGRESS


def _next_id(prefix: str) -> str:
    return f"{prefix}-{next(_IDS)}"


def _user(text: str, *, entry_id: str | None = None) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id or _next_id("user"),
        session_id="s1",
        turn_id="t1",
        created_at=0,
        updated_at=0,
        generation_status=_COMPLETED,
        role="user",
        content=[TextContentBlock(text=text)],
        source="turn_start",
    )


def _blocked_user(text: str) -> PublicMessageEntry:
    return _user(text).model_copy(
        update={"outcome": DiscardedGenerationOutcome(cause="skipped")}
    )


def _assistant(
    text: str,
    *,
    entry_id: str | None = None,
    status: PublicEntryGenerationStatus = _COMPLETED,
) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id or _next_id("assistant"),
        session_id="s1",
        turn_id="t1",
        created_at=0,
        updated_at=0,
        generation_status=status,
        role="assistant",
        content=[TextContentBlock(text=text)],
        source="harness",
        outcome=CommittedGenerationOutcome(),
    )


def _completed(output_text: str) -> CompletedEffectState:
    return CompletedEffectState(
        output_text=output_text,
        display=EffectResultDisplay(success=True, message="done"),
    )


def _failed(message: str) -> FailedEffectState:
    return FailedEffectState(
        error=PublicError(message=message, code="tool_error"),
        display=EffectResultDisplay(success=False, message="failed"),
    )


def _effect(
    *,
    tool_name: str = "gh",
    arguments: JsonValue = None,
    state: EffectState | None = None,
    entry_id: str | None = None,
    status: PublicEntryGenerationStatus = _COMPLETED,
) -> PublicEffectEntry:
    return PublicEffectEntry(
        id=entry_id or _next_id("effect"),
        session_id="s1",
        turn_id="t1",
        created_at=0,
        updated_at=0,
        generation_status=status,
        title=tool_name,
        detail=GenericEffectDetail(
            tool_name=tool_name,
            input=arguments,
            display=EffectCallDisplay(summary=tool_name, status_text=tool_name),
        ),
        state=state or _completed(""),
    )


def _compaction(
    *, entry_id: str = "c1", status: PublicEntryGenerationStatus = _COMPLETED
) -> PublicCheckpointEntry:
    return PublicCheckpointEntry(
        id=entry_id,
        session_id="s1",
        created_at=0,
        updated_at=0,
        generation_status=status,
        kind="compaction",
    )


def _notice() -> PublicNoticeEntry:
    return PublicNoticeEntry(
        id=_next_id("notice"),
        session_id="s1",
        created_at=0,
        updated_at=0,
        generation_status=_COMPLETED,
        level="info",
        message="Title updated",
        detail=SessionTitleUpdatedNoticeDetail(title="Old"),
    )


def _steps(n: int) -> list[PublicHistoryEntry]:
    return [_assistant(f"step {index}") for index in range(n)]


# Transcript


def test_transcript_reads_user_and_assistant_messages_and_skips_the_rest() -> None:
    # Prepare
    entries: list[PublicHistoryEntry] = [
        _notice(),
        _user("Fix the Stripe webhook retry logic"),
        _assistant("I'll look at the webhook handler."),
        _compaction(),
    ]

    # Do
    transcript = build_title_transcript(entries)

    # Assert
    assert transcript == (
        "user: Fix the Stripe webhook retry logic"
        "\n\nassistant: I'll look at the webhook handler."
    )


def test_transcript_reads_user_messages_as_shown_and_leaves_out_blocked_ones() -> None:
    """*Prepare*: History with a user message a hook blocked between two others.
    *Do*: Build the title transcript.
    *Assert*: The other messages read as history shows them; the blocked one,
        which the agent's model never saw, is left out.
    """
    # Prepare
    entries: list[PublicHistoryEntry] = [
        _user("my key is sk-123"),
        _assistant("ok"),
        _blocked_user("my other key is sk-456"),
        _user("Explain the retry loop"),
    ]

    # Do
    transcript = build_title_transcript(entries)

    # Assert
    assert transcript == (
        "user: my key is sk-123\n\nassistant: ok\n\nuser: Explain the retry loop"
    )


def test_transcript_renders_tool_input_and_output() -> None:
    # Prepare
    entries: list[PublicHistoryEntry] = [
        _user("please review https://github.com/acme/app/pull/482"),
        _assistant("I'll fetch the PR details."),
        _effect(
            tool_name="gh",
            arguments={"command": "pr view 482 --json title,body"},
            state=_completed(
                'title: "Fix session title renaming"\nbody: Titles drift...'
            ),
        ),
    ]

    # Do
    transcript = build_title_transcript(entries)

    # Assert
    assert 'tool(gh): {"command": "pr view 482 --json title,body"}' in transcript
    assert 'tool result(gh): title: "Fix session title renaming"' in transcript
    assert "body: Titles drift..." in transcript


def test_transcript_renders_a_typed_tool_input_without_unset_fields() -> None:
    # Prepare
    entry = PublicEffectEntry(
        id="effect-shell",
        session_id="s1",
        created_at=0,
        updated_at=0,
        generation_status=_COMPLETED,
        title="bash",
        detail=ShellEffectDetail(
            tool_name="bash",
            input=ShellEffectInput(command="pytest -k login"),
            display=EffectCallDisplay(summary="bash", status_text="bash"),
        ),
        state=_completed("1 failed"),
    )

    # Do
    transcript = build_title_transcript([entry])

    # Assert
    assert transcript == (
        'tool(bash): {"command": "pytest -k login"}\ntool result(bash): 1 failed'
    )


def test_transcript_preserves_chronological_order() -> None:
    # Prepare
    entries: list[PublicHistoryEntry] = [
        _user("first"),
        _effect(
            tool_name="read", arguments={"path": "a.py"}, state=_completed("contents")
        ),
        _assistant("second"),
    ]

    # Do
    transcript = build_title_transcript(entries)

    # Assert
    assert transcript.index("user: first") < transcript.index("tool(read):")
    assert transcript.index("tool(read):") < transcript.index("tool result(read):")
    assert transcript.index("tool result(read):") < transcript.index(
        "assistant: second"
    )


def test_transcript_tool_line_edges() -> None:
    # Prepare
    long_output = _effect(tool_name="read", state=_completed("x" * 1000))
    failure = _effect(tool_name="gh", state=_failed("gh: not found"))
    empty_args = _effect(tool_name="gh", arguments={}, state=_completed("done"))
    running = _effect(
        tool_name="gh",
        state=RunningEffectState(output_text="partial"),
        status=_IN_PROGRESS,
    )

    # Do
    truncated = build_title_transcript(
        [long_output], policy=TitlePolicy(max_tool_chars=100)
    )
    failed = build_title_transcript([failure])
    no_args = build_title_transcript([empty_args])
    partial = build_title_transcript([running])

    # Assert
    assert "x" * 100 in truncated
    assert "x" * 101 not in truncated
    assert failed == "tool result(gh) failed: gh: not found"
    assert no_args == "tool result(gh): done"
    assert partial == "tool result(gh): partial"


def test_transcript_message_limits() -> None:
    # Prepare
    long_message = [_user("x" * 5000)]
    long_transcript = [_user("a" * 300)]

    # Do
    empty = build_title_transcript([])
    capped = build_title_transcript(
        long_message, policy=TitlePolicy(max_message_chars=100)
    )
    elided = build_title_transcript(
        long_transcript,
        policy=TitlePolicy(
            max_transcript_chars=200, head_transcript_chars=80, max_message_chars=5000
        ),
    )

    # Assert
    assert empty == ""
    assert len(capped.split(": ", 1)[1]) == 100
    assert elided.startswith("user: " + "a" * 74 + "\n\n[…]\n\n")
    assert elided.endswith("a" * 120)
    assert len(elided) == 200 + len("\n\n[…]\n\n")


# Cleaning


def test_clean_title_normalizes_the_model_output() -> None:
    assert clean_title('"Fix webhook retries"') == "Fix webhook retries"
    assert clean_title("  Fix   webhook  retries  ") == "Fix webhook retries"
    assert clean_title("Fix webhook retries\n\nExtra stuff") == "Fix webhook retries"
    assert clean_title("Fix\x00webhook") == "Fixwebhook"


def test_clean_title_rejects_or_truncates_unusable_titles() -> None:
    assert clean_title(None) is None
    assert clean_title("") is None
    assert clean_title("New session") is None
    assert clean_title("Untitled") is None
    truncated = clean_title("A" * 100, policy=TitlePolicy(max_title_chars=20))
    assert truncated == "A" * 20 + "…"


# Cadence


def test_cadence_first_title_is_due_at_the_first_turn_end() -> None:
    # Prepare
    empty = TitleCadence()
    one_step = TitleCadence([_assistant("hi")])

    # Do
    due_without_steps = empty.begin_if_due(current_title=None, turn_ended=True)
    due_after_one_step = one_step.begin_if_due(current_title=None, turn_ended=True)

    # Assert
    assert due_without_steps
    assert due_after_one_step


def test_cadence_first_title_is_due_mid_turn_at_the_step_floor() -> None:
    # Prepare
    cadence = TitleCadence(policy=TitlePolicy(initial_max_steps=3))
    due: list[bool] = []

    # Do
    for entry in _steps(3):
        cadence.observe(entry)
        due.append(cadence.begin_if_due(current_title=None))

    # Assert
    assert due == [False, False, True]


def test_cadence_counts_only_completed_assistant_messages_and_informative_tools() -> (
    None
):
    """*Prepare*: History with a user message, a streaming assistant message, a
        running tool, a completed tool with blank output, a failed tool with an
        error but no output, and three informative steps.
    *Do*: Count steps.
    *Assert*: Only the completed assistant messages and the completed tool call
        with output count.
    """
    # Prepare
    entries: list[PublicHistoryEntry] = [
        _user("hello"),
        _assistant("hi"),
        _assistant("streaming", status=_IN_PROGRESS),
        _effect(state=RunningEffectState(output_text="..."), status=_IN_PROGRESS),
        _effect(state=_completed("  \n ")),
        _effect(state=_failed("boom")),
        _effect(state=_completed("PR 4821: feat(billing)...")),
        _assistant("more"),
    ]

    # Do
    cadence = TitleCadence(entries)

    # Assert
    assert cadence.steps == 3


def test_cadence_counts_an_entry_reported_twice_once() -> None:
    # Prepare
    cadence = TitleCadence(policy=TitlePolicy(initial_max_steps=3))
    streaming = _assistant("partial", entry_id="a1", status=_IN_PROGRESS)
    completed = _assistant("partial and the rest", entry_id="a1")

    # Do
    cadence.observe(streaming)
    cadence.observe(completed)
    cadence.observe(completed)
    cadence.observe(_effect(entry_id="e1", state=_completed("out")))
    cadence.observe(_effect(entry_id="e1", state=_completed("out, updated")))

    # Assert
    assert cadence.steps == 2
    assert not cadence.begin_if_due(current_title=None)


def test_cadence_mid_turn_waits_for_tool_results_with_content() -> None:
    """*Prepare*: A Turn of three tool calls with empty output.
    *Do*: Ask whether a title is due mid-turn, then report three informative
        results, then end a Turn of only empty calls in another Session.
    *Assert*: Empty calls do not count mid-turn; informative ones do; a Turn end
        still gets its title with only empty calls.
    """
    # Prepare
    cadence = TitleCadence([_effect(state=_completed("")) for _ in range(3)])
    other = TitleCadence([_effect(state=_completed("")) for _ in range(3)])

    # Do
    due_on_empty_calls = cadence.begin_if_due(current_title=None)
    for output in ("PR 4821: feat(billing)...", "file a", "file b"):
        cadence.observe(_effect(state=_completed(output)))
    due_on_results = cadence.begin_if_due(current_title=None)
    due_at_turn_end = other.begin_if_due(current_title=None, turn_ended=True)

    # Assert
    assert not due_on_empty_calls
    assert due_on_results
    assert due_at_turn_end


def test_cadence_compaction_brings_the_first_title_forward() -> None:
    # Prepare
    cadence = TitleCadence([_assistant("one")], policy=TitlePolicy(initial_max_steps=3))
    due_before = cadence.begin_if_due(current_title=None)

    # Do
    cadence.observe(_compaction(status=_IN_PROGRESS))
    due_while_compacting = cadence.begin_if_due(current_title=None)
    cadence.observe(_compaction())
    due_after = cadence.begin_if_due(current_title=None)

    # Assert
    assert not due_before
    assert not due_while_compacting
    assert due_after


def test_cadence_compaction_does_not_refresh_an_existing_title() -> None:
    # Prepare
    cadence = TitleCadence([_assistant("one")])
    assert cadence.begin_if_due(current_title=None, turn_ended=True)
    cadence.record(title="First")

    # Do
    cadence.observe(_assistant("two"))
    cadence.observe(_compaction(entry_id="c2"))
    due_after_compaction = cadence.begin_if_due(current_title="First", turn_ended=True)
    for entry in _steps(5):
        cadence.observe(entry)
    due_after_interval = cadence.begin_if_due(current_title="First")

    # Assert
    assert not due_after_compaction
    assert due_after_interval


def test_cadence_confirmation_is_due_once_six_steps_after_the_first_attempt() -> None:
    """*Prepare*: A Session titled after its first step.
    *Do*: Add steps one by one up to well past the interval, asking each time.
    *Assert*: One confirmation is due exactly six steps after the first
        attempt, mid-turn; after it the title is final.
    """
    # Prepare
    cadence = TitleCadence([_assistant("one")])
    assert cadence.begin_if_due(current_title=None, turn_ended=True)
    cadence.record(title="First")
    due_at: list[int] = []

    # Do
    for entry in _steps(50):
        cadence.observe(entry)
        title = "Second" if due_at else "First"
        if cadence.begin_if_due(current_title=title):
            due_at.append(cadence.steps)
            cadence.record(title="Second")
    final = cadence.begin_if_due(current_title="Second", turn_ended=True)

    # Assert
    assert due_at == [7]
    assert not final


def test_cadence_a_failed_attempt_counts_and_the_next_due_point_is_the_last() -> None:
    """*Prepare*: A Session whose first title call returns nothing usable.
    *Do*: Ask again at once, at the next Turn end, six steps later, and past it.
    *Assert*: The failed attempt counts: nothing is due until six steps later,
        which is the second and last attempt.
    """
    # Prepare
    cadence = TitleCadence([_assistant("one")])
    assert cadence.begin_if_due(current_title=None, turn_ended=True)

    # Do
    due_at_once = cadence.begin_if_due(current_title=None, turn_ended=True)
    for entry in _steps(6):
        cadence.observe(entry)
    due_later = cadence.begin_if_due(current_title=None)
    for entry in _steps(100):
        cadence.observe(entry)
    due_past_the_budget = cadence.begin_if_due(current_title=None, turn_ended=True)

    # Assert
    assert not due_at_once
    assert due_later
    assert not due_past_the_budget


def test_cadence_attempt_budget_follows_the_policy() -> None:
    # Prepare
    cadence = TitleCadence(
        policy=TitlePolicy(capped_max_generations=1, initial_max_steps=1)
    )
    cadence.observe(_assistant("one"))
    assert cadence.begin_if_due(current_title=None)
    cadence.record(title="First")

    # Do
    for entry in _steps(100):
        cadence.observe(entry)
    due = cadence.begin_if_due(current_title="First", turn_ended=True)

    # Assert
    assert not due


def test_cadence_a_rename_or_a_title_it_did_not_generate_stops_it() -> None:
    # Prepare
    renamed = TitleCadence([_assistant("one")])
    assert renamed.begin_if_due(current_title=None, turn_ended=True)
    renamed.record(title="Auto title")
    reopened_with_a_title = TitleCadence(_steps(100))

    # Do
    for entry in _steps(10):
        renamed.observe(entry)
    renamed_due = renamed.begin_if_due(
        current_title="User renamed this", turn_ended=True
    )
    reopened_due = reopened_with_a_title.begin_if_due(
        current_title="Existing title", turn_ended=True
    )

    # Assert
    assert not renamed.is_manual("Auto title")
    assert renamed.is_manual("User renamed this")
    assert not renamed_due
    assert not reopened_due


def test_cadence_reset_recounts_steps_from_the_remaining_history() -> None:
    """*Prepare*: A titled Session at ten steps, first attempt at step one.
    *Do*: Rewind to three steps, then add steps until a confirmation is due.
    *Assert*: Steps count from the history that remains, and the steps the
        rewind removed no longer separate the last attempt from the next one.
    """
    # Prepare
    history: list[PublicHistoryEntry] = [_assistant("one")]
    cadence = TitleCadence(history)
    assert cadence.begin_if_due(current_title=None, turn_ended=True)
    cadence.record(title="First")
    history += _steps(9)
    for entry in history[1:]:
        cadence.observe(entry)
    assert cadence.steps == 10

    # Do
    cadence.reset(history[:3])
    steps_after_rewind = cadence.steps
    due_at: list[int] = []
    for entry in _steps(10):
        cadence.observe(entry)
        if cadence.begin_if_due(current_title="First"):
            due_at.append(cadence.steps)

    # Assert
    assert steps_after_rewind == 3
    assert due_at == [7]


def test_cadence_reset_below_the_last_attempt_waits_a_full_interval() -> None:
    """*Prepare*: A Session titled at five steps.
    *Do*: Rewind to two steps, then add steps until a confirmation is due.
    *Assert*: The last attempt now sits at two steps, so the confirmation is
        due six steps later, at eight.
    """
    # Prepare
    cadence = TitleCadence(_steps(5))
    assert cadence.begin_if_due(current_title=None, turn_ended=True)
    cadence.record(title="First")

    # Do
    cadence.reset(_steps(2))
    due_at: list[int] = []
    for entry in _steps(10):
        cadence.observe(entry)
        if cadence.begin_if_due(current_title="First"):
            due_at.append(cadence.steps)

    # Assert
    assert due_at == [8]


# Generation


class _RecordingCompletion:
    def __init__(self, *replies: str | None, error: Exception | None = None) -> None:
        self._replies = list(replies)
        self._error = error
        self.requests: list[TitleRequest] = []

    async def __call__(self, request: TitleRequest) -> str | None:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return self._replies.pop(0) if self._replies else None


@pytest.mark.asyncio
async def test_generate_sends_the_transcript_and_returns_the_cleaned_title() -> None:
    """*Prepare*: A short history and a model that answers with a quoted title
        and an explanation.
    *Do*: Generate a title.
    *Assert*: The model gets the transcript with the policy's limits, and the
        title comes back cleaned.
    """
    # Prepare
    entries: list[PublicHistoryEntry] = [
        _user("the orders endpoint does N+1 queries on customers"),
        _assistant("Looking at the serializer."),
    ]
    complete = _RecordingCompletion('"Orders endpoint N+1 queries"\nbecause...')

    # Do
    title = await generate_session_title(
        entries, previous_title=None, complete=complete
    )

    # Assert
    assert title == "Orders endpoint N+1 queries"
    assert complete.requests == [
        TitleRequest(
            user_content=(
                "user: the orders endpoint does N+1 queries on customers"
                "\n\nassistant: Looking at the serializer."
            ),
            max_tokens=1024,
            request_timeout_seconds=6.0,
            retry_budget_seconds=10.0,
        )
    ]


@pytest.mark.asyncio
async def test_generate_gives_the_model_the_current_title() -> None:
    """*Prepare*: A titled Session.
    *Do*: Generate a title with its current title.
    *Assert*: The model reads the current title ahead of the transcript.
    """
    # Prepare
    entries: list[PublicHistoryEntry] = [_user("move models to pydantic v2")]
    complete = _RecordingCompletion("Pydantic v2 migration")

    # Do
    await generate_session_title(
        entries, previous_title="Pydantic upgrade", complete=complete
    )

    # Assert
    [request] = complete.requests
    assert request.user_content == (
        "Current title: Pydantic upgrade\n\n"
        "Transcript:\nuser: move models to pydantic v2"
    )


@pytest.mark.asyncio
async def test_generate_skips_the_call_on_an_empty_transcript() -> None:
    """*Prepare*: History with only a notice and a blocked user message.
    *Do*: Generate a title.
    *Assert*: No call is made and there is no title.
    """
    # Prepare
    complete = _RecordingCompletion("Never asked")

    # Do
    title = await generate_session_title(
        [_notice(), _blocked_user("secret")], previous_title=None, complete=complete
    )

    # Assert
    assert title is None
    assert complete.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "complete",
    [
        _RecordingCompletion(None),
        _RecordingCompletion("New session"),
        _RecordingCompletion(error=RuntimeError("provider down")),
    ],
    ids=["no reply", "generic reply", "failed call"],
)
async def test_generate_returns_none_without_a_usable_title(
    complete: _RecordingCompletion,
) -> None:
    """*Prepare*: A model that answers nothing, a generic title, or fails.
    *Do*: Generate a title.
    *Assert*: The one call is made and there is no title.
    """
    # Prepare
    entries: list[PublicHistoryEntry] = [_user("hello")]

    # Do
    title = await generate_session_title(
        entries, previous_title=None, complete=complete
    )

    # Assert
    assert title is None
    assert len(complete.requests) == 1


@pytest.mark.asyncio
async def test_generate_gives_up_past_the_total_time_limit() -> None:
    """*Prepare*: A model that never answers, under a short total limit.
    *Do*: Generate a title.
    *Assert*: It gives up with no title once the limit passes.
    """
    # Prepare
    never = asyncio.Event()
    started: list[TitleRequest] = []

    async def complete(request: TitleRequest) -> str | None:
        started.append(request)
        await never.wait()
        return "Too late"

    # Do
    title = await generate_session_title(
        [_user("hello")],
        previous_title=None,
        complete=complete,
        policy=TitlePolicy(total_timeout_seconds=0.05),
    )

    # Assert
    assert title is None
    assert len(started) == 1
