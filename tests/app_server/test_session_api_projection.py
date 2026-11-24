"""Vibe's public history kept in step with Session API history events."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    CheckpointHistoryEntry,
    HistoryEntry,
    HistoryEntryAddedEvent,
    HistoryEntryCompletedEvent,
    HistoryEntryPatchedEvent,
    JsonPatchAppendOperation,
    JsonPatchOperation as ApiJsonPatchOperation,
    JsonPatchReplaceOperation,
    MessageHistoryEntry,
    NoticeHistoryEntry,
    TextContentBlock,
    ToolCallHistoryEntry,
    ToolResultHistoryEntry,
    UnknownHistoryEntry,
)
from vibe.app_server import _session_api_projection
from vibe.app_server._session_api_projection import ApiHistoryProjection, EntryChange
from vibe.app_server._unified_history_projection import project_raw_history_entry
from vibe.app_server.models import (
    AnsweredCallbackState,
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    UserAnswer,
    UserInputCallbackOutput,
    UserQuestionResult,
)
from vibe.core.tools.builtins.skill import skill_content_marker

SESSION_ID = "session-1"
ROOT = Path("/work")
FILE = "/work/src/app.py"

type HistoryEvent = (
    HistoryEntryAddedEvent | HistoryEntryPatchedEvent | HistoryEntryCompletedEvent
)
type Step = tuple[HistoryEvent, HistoryEntry]


def _base(entry_id: str, index: int, *, generating: bool = False) -> dict[str, Any]:
    return {
        "id": entry_id,
        "index": index,
        "turn_id": "turn-1",
        "generation_status": "generating" if generating else "complete",
        "created_at": 10 + index,
        "updated_at": 10 + index,
    }


def _skill_message() -> MessageHistoryEntry:
    body = f"{skill_content_marker('lint')}\n# Skill: lint\n\nDo it.\n</skill_content>"
    return MessageHistoryEntry(
        **_base("user-1", 0),
        role="user",
        content=[TextContentBlock(text="/lint the app"), TextContentBlock(text=body)],
        annotations={
            "vibe.source": "turn_start",
            "vibe.userDisplayContent": {
                "version": "1",
                "host": "vibe",
                "content": [{"type": "vibe.skill_invocation", "name": "lint"}],
            },
        },
    )


def _assistant(text: str, *, generating: bool, updated_at: int) -> MessageHistoryEntry:
    return MessageHistoryEntry(
        **{**_base("assistant-1", 1, generating=generating), "updated_at": updated_at},
        role="assistant",
        content=[TextContentBlock(text=text)],
    )


def _tool_call() -> ToolCallHistoryEntry:
    return ToolCallHistoryEntry(
        **_base("effect-read:call", 2),
        tool_call_id="call-read",
        tool_kind="local",
        name="read_file",
        arguments={"path": FILE},
    )


def _effect_display(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "detail": {
            "kind": "tool",
            "toolName": "read_file",
            "input": {"path": FILE},
            "display": {"summary": "read_file", "statusText": "Reading"},
        },
        "state": state,
        "title": "read_file",
    }


def _running_result(output_text: str, updated_at: int) -> ToolResultHistoryEntry:
    return ToolResultHistoryEntry(
        **{**_base("effect-read:result", 3, generating=True), "updated_at": updated_at},
        tool_call_id="call-read",
        name="read_file",
        result=output_text,
        annotations={
            "vibe.effect": _effect_display({
                "status": "running",
                "outputText": output_text,
            })
        },
    )


def _completed_result() -> ToolResultHistoryEntry:
    return ToolResultHistoryEntry(
        **{**_base("effect-read:result", 3), "updated_at": 30},
        tool_call_id="call-read",
        name="read_file",
        result={
            "structured_content": {
                "path": FILE,
                "content": "print('hi')\n",
                "offset": 0,
                "lines_read": 1,
            }
        },
        annotations={
            "vibe.effect": _effect_display({
                "status": "completed",
                "outputText": "print('hi')\n",
                "display": {"success": True, "message": "1 line"},
            })
        },
    )


def _checkpoint() -> CheckpointHistoryEntry:
    return CheckpointHistoryEntry(
        **_base("checkpoint-1", 4),
        checkpoint_kind="custom",
        message="Rewound",
        annotations={"vibe.checkpointKind": "rewind"},
    )


def _notices() -> list[NoticeHistoryEntry]:
    return [
        NoticeHistoryEntry(
            **_base("notice-1", 5),
            level="info",
            message="Agent changed",
            details={"kind": "agent_changed", "agentName": "plan"},
        ),
        NoticeHistoryEntry(
            **_base("notice-2", 6),
            level="info",
            message="Loop fired",
            details={"kind": "scheduled_loop_fired", "loopId": "loop-1"},
            annotations={"vibe.noticeDetailsKey": "details"},
        ),
    ]


def _patched(after: HistoryEntry, patches: Sequence[ApiJsonPatchOperation]) -> Step:
    event = HistoryEntryPatchedEvent(
        entry_id=after.id, entry_index=after.index, patches=list(patches)
    )
    return event, after


def _steps() -> list[Step]:
    """A Turn as the Session API streams it: each event, then the entry it leaves."""
    message = _skill_message()
    started = _assistant("", generating=True, updated_at=11)
    streamed = _assistant("Hi", generating=True, updated_at=12)
    answered = _assistant("Hi!", generating=True, updated_at=13)
    finished = _assistant("Hi!", generating=False, updated_at=14)
    running = _running_result("", 13)
    output = _running_result("print(", 20)
    return [
        (HistoryEntryAddedEvent(entry=message), message),
        (HistoryEntryAddedEvent(entry=started), started),
        _patched(
            streamed,
            [
                JsonPatchAppendOperation(path="/content/0/text", value="Hi"),
                JsonPatchReplaceOperation(path="/updatedAt", value=12),
            ],
        ),
        _patched(
            answered,
            [
                JsonPatchAppendOperation(path="/content/0/text", value="!"),
                JsonPatchReplaceOperation(path="/updatedAt", value=13),
            ],
        ),
        (HistoryEntryCompletedEvent(entry=finished), finished),
        (HistoryEntryAddedEvent(entry=_tool_call()), _tool_call()),
        (HistoryEntryAddedEvent(entry=running), running),
        _patched(
            output,
            [
                JsonPatchAppendOperation(path="/result", value="print("),
                JsonPatchAppendOperation(
                    path="/annotations/vibe.effect/state/outputText", value="print("
                ),
                JsonPatchReplaceOperation(path="/updatedAt", value=20),
            ],
        ),
        (HistoryEntryCompletedEvent(entry=_completed_result()), _completed_result()),
        (HistoryEntryAddedEvent(entry=_checkpoint()), _checkpoint()),
        *((HistoryEntryAddedEvent(entry=notice), notice) for notice in _notices()),
    ]


def _entries(steps: Sequence[Step]) -> list[HistoryEntry]:
    entries: dict[str, HistoryEntry] = {}
    for _event, entry in steps:
        entries[entry.id] = entry
    return list(entries.values())


def _has_call_without_result(entries: Sequence[HistoryEntry]) -> bool:
    results = {
        entry.tool_call_id
        for entry in entries
        if isinstance(entry, ToolResultHistoryEntry)
    }
    return any(
        isinstance(entry, ToolCallHistoryEntry) and entry.tool_call_id not in results
        for entry in entries
    )


def _projected_at_once(
    entries: Sequence[HistoryEntry], display_roots: Sequence[Path] = (ROOT,)
) -> list[PublicHistoryEntry]:
    """Each entry's runtime-format record projected as the adapter does, at once."""
    ordered = sorted(entries, key=lambda entry: entry.index)
    results = {
        entry.tool_call_id: entry
        for entry in ordered
        if isinstance(entry, ToolResultHistoryEntry)
    }
    history: list[PublicHistoryEntry] = []
    for entry in ordered:
        if isinstance(entry, ToolResultHistoryEntry) or (
            isinstance(entry, ToolCallHistoryEntry)
            and entry.tool_call_id not in results
        ):
            continue
        result = (
            results.get(entry.tool_call_id)
            if isinstance(entry, ToolCallHistoryEntry)
            else None
        )
        raw = _session_api_projection._raw_entry(entry, SESSION_ID, result)
        if raw is not None:
            history.extend(project_raw_history_entry(raw, tuple(display_roots)))
    return history


def _projection(entries: Sequence[HistoryEntry] = ()) -> ApiHistoryProjection:
    projection = ApiHistoryProjection(SESSION_ID, [ROOT])
    projection.reset(entries)
    return projection


def _raw_ids(changes: Sequence[EntryChange]) -> list[object]:
    return [change.raw["id"] if change.raw else None for change in changes]


def _public(projection: ApiHistoryProjection, entry_id: str) -> dict[str, Any]:
    entry = next(entry for entry in projection.history if entry.id == entry_id)
    return entry.model_dump(mode="json", by_alias=True)


def test_reset_gives_the_history_a_read_projects() -> None:
    """*Prepare*: A Turn with a skill invocation, a streamed answer, a tool
        call and its result, a checkpoint and two notices.
    *Do*: Reset a projection from those Session API entries.
    *Assert*: The public history equals every entry projected at once, as a
        read gives it, and covers every kind of entry.
    """
    # Prepare
    entries = _entries(_steps())

    # Do
    history = ApiHistoryProjection(SESSION_ID, [ROOT]).reset(entries)

    # Assert
    assert history == _projected_at_once(entries)
    assert [entry.id for entry in history] == [
        "user-1",
        "user-1:invoked-skill:lint",
        "assistant-1",
        "effect-read",
        "checkpoint-1",
        "notice-1",
        "notice-2",
    ]


def test_events_applied_one_by_one_give_the_same_history_as_a_reset() -> None:
    """*Prepare*: An empty projection and the Turn's events.
    *Do*: Apply the events one at a time.
    *Assert*: After every event, the history equals a reset from the entries
        so far, and the same entries projected at once whenever they can be:
        that projection fails on a tool call whose result is not there yet.
    """
    # Prepare
    steps = _steps()
    projection = _projection()

    for count, (event, _entry) in enumerate(steps, start=1):
        # Do
        projection.apply(event)

        # Assert
        entries = _entries(steps[:count])
        assert projection.history == _projection(entries).history
        if not _has_call_without_result(entries):
            assert projection.history == _projected_at_once(entries)


def test_a_tool_call_shows_once_its_result_arrives() -> None:
    """*Prepare*: A projection holding the user message.
    *Do*: Apply the tool call, then its first result.
    *Assert*: The call alone changes nothing; the result adds the effect,
        reported with the call's runtime-format record.
    """
    # Prepare
    projection = _projection([_skill_message()])

    # Do
    on_call = projection.apply(HistoryEntryAddedEvent(entry=_tool_call()))
    on_result = projection.apply(HistoryEntryAddedEvent(entry=_running_result("", 13)))

    # Assert
    assert on_call == []
    assert _raw_ids(on_result) == ["effect-read"]
    assert projection.history[-1].id == "effect-read"


@pytest.mark.parametrize(
    ("step", "entry_id", "path", "value"),
    [
        pytest.param(3, "assistant-1", ("content", 0, "text"), "Hi!", id="text"),
        pytest.param(7, "effect-read", ("state", "outputText"), "print(", id="tool"),
    ],
)
def test_a_streamed_patch_changes_only_its_entry(
    step: int, entry_id: str, path: tuple[str | int, ...], value: str
) -> None:
    """*Prepare*: A projection holding the Turn up to one streamed patch.
    *Do*: Apply that patch.
    *Assert*: Only the patched entry is reported, and the history shows the
        streamed text.
    """
    # Prepare
    steps = _steps()
    projection = _projection(_entries(steps[:step]))

    # Do
    changes = projection.apply(steps[step][0])

    # Assert
    assert _raw_ids(changes) == [entry_id]
    shown: Any = _public(projection, entry_id)
    for key in path:
        shown = shown[key]
    assert shown == value


def test_a_tool_result_changes_only_the_effect_it_settles() -> None:
    """*Prepare*: A projection holding the whole Turn with the tool running.
    *Do*: Apply the result's completion.
    *Assert*: Only the tool's effect changed, from running to completed, with
        its path shown relative to the workspace.
    """
    # Prepare
    steps = _steps()
    projection = _projection(_entries([*steps[:8], *steps[9:]]))
    assert _public(projection, "effect-read")["state"]["status"] == "running"

    # Do
    changes = projection.apply(steps[8][0])

    # Assert
    assert _raw_ids(changes) == ["effect-read"]
    state = _public(projection, "effect-read")["state"]
    assert state["status"] == "completed"
    assert state["display"]["message"] == "1 line from src/app.py"


def test_a_tool_result_with_a_status_a_newer_host_added_shows_completed() -> None:
    """*Prepare*: A tool call and its result, whose generation status is one
        this reader does not know.
    *Do*: Project the call with its result.
    *Assert*: The effect is completed, as a message with that status is.
    """
    # Prepare
    result = ToolResultHistoryEntry.model_validate({
        **_completed_result().model_dump(by_alias=True),
        "generationStatus": "archived",
    })
    message = MessageHistoryEntry.model_validate({
        **_skill_message().model_dump(by_alias=True),
        "generationStatus": "archived",
    })

    # Do
    effect = _session_api_projection._raw_entry(_tool_call(), SESSION_ID, result)
    shown = _session_api_projection._raw_entry(message, SESSION_ID, None)

    # Assert
    assert str(result.generation_status) == "archived"
    assert effect is not None and shown is not None
    assert effect["generationStatus"] == shown["generationStatus"] == "completed"


def test_a_skill_invocation_adds_its_message_and_skill_effect() -> None:
    """*Prepare*: An empty projection.
    *Do*: Apply the user message that invokes the lint skill.
    *Assert*: Two public entries are added from the one message record: the
        message and the skill effect.
    """
    # Prepare
    projection = _projection()

    # Do
    changes = projection.apply(HistoryEntryAddedEvent(entry=_skill_message()))

    # Assert
    assert _raw_ids(changes) == ["user-1", "user-1"]
    assert [entry.id for entry in projection.history] == [
        "user-1",
        "user-1:invoked-skill:lint",
    ]


def test_an_unchanged_entry_reports_no_change_and_keeps_its_object() -> None:
    """*Prepare*: A projection holding the Turn.
    *Do*: Apply a completion that repeats a notice as it already is.
    *Assert*: Nothing changed, and the history keeps the same entry object.
    """
    # Prepare
    entries = _entries(_steps())
    projection = _projection(entries)
    before = projection.history[-1]

    # Do
    changes = projection.apply(HistoryEntryCompletedEvent(entry=_notices()[1]))

    # Assert
    assert changes == []
    assert projection.history[-1] is before


def test_a_patch_for_an_entry_outside_the_page_changes_nothing() -> None:
    """*Prepare*: A projection reset from a page without the answer.
    *Do*: Apply a patch to the answer.
    *Assert*: Nothing changed and the history is unchanged.
    """
    # Prepare
    projection = _projection([_skill_message()])
    before = projection.history

    # Do
    changes = projection.apply(_steps()[3][0])

    # Assert
    assert changes == []
    assert projection.history == before


def test_a_settled_question_in_history_shows_as_its_callback() -> None:
    """*Prepare*: A settled question as the Session API carries it: an entry
        of an unknown type, ``callback``.
    *Do*: Reset a projection from it.
    *Assert*: The public history holds the answered callback, with its request
        and answer, as a resumed Session shows it.
    """
    # Prepare
    request = {
        "questions": [
            {"question": "Ship it?", "options": [{"label": "Yes"}, {"label": "No"}]}
        ]
    }
    output = UserInputCallbackOutput(
        result=UserQuestionResult(
            answers=[UserAnswer(question="Ship it?", answer="Yes")]
        )
    )
    entry = UnknownHistoryEntry.model_validate({
        **_base("user-input-1", 0),
        "annotations": {"vibe.relatedEntryId": "question-1"},
        "type": "callback",
        "callbackId": "user-input-1",
        "title": "User input required",
        "detail": {"kind": "user_input", "request": request},
        "state": {
            "status": "answered",
            "output": output.model_dump(mode="json", by_alias=True),
        },
    })

    # Do
    [callback] = _projection([entry]).history

    # Assert
    assert isinstance(callback, PublicCallbackEntry)
    assert callback.callback_id == "user-input-1"
    assert callback.related_entry_id == "question-1"
    assert callback.generation_status is PublicEntryGenerationStatus.COMPLETED
    assert callback.state == AnsweredCallbackState(output=output)


def test_moved_display_roots_reproject_the_paths_they_show() -> None:
    """*Prepare*: A projection holding the Turn, with paths shown under /work.
    *Do*: Move the display roots to /work/src.
    *Assert*: Only the tool's effect changed, and the history equals the
        entries projected at once under the new roots.
    """
    # Prepare
    entries = _entries(_steps())
    projection = _projection(entries)
    moved = [ROOT / "src"]

    # Do
    changes = projection.set_display_roots(moved)

    # Assert
    assert _raw_ids(changes) == ["effect-read"]
    assert projection.history == _projected_at_once(entries, moved)


def test_a_change_mid_history_splices_it_and_reports_the_unchanged_prefix() -> None:
    """*Prepare*: A projection holding the Turn without the tool's result, so
        the call shows nothing yet, with entries after it; its history taken.
    *Do*: Apply the result, then take the history twice.
    *Assert*: The effect is spliced in where the call is; the first take says
        the entries before it are the objects the previous take returned, and
        the second take, with no event between, says all of them are.
    """
    # Prepare
    entries = [
        entry
        for entry in _entries(_steps())
        if not isinstance(entry, ToolResultHistoryEntry)
    ]
    projection = _projection(entries)
    before, unchanged_before = projection.take_history()

    # Do
    projection.apply(HistoryEntryCompletedEvent(entry=_completed_result()))
    after, unchanged = projection.take_history()
    again, unchanged_again = projection.take_history()

    # Assert
    assert unchanged_before == 0
    assert after == _projected_at_once([*entries, _completed_result()])
    position = [entry.id for entry in after].index("effect-read")
    assert unchanged == position
    assert all(old is new for old, new in zip(before[:position], after, strict=False))
    assert after[position + 1 :] == before[position:]
    assert (again, unchanged_again) == (after, len(after))


class _RecordingApps:
    """``SessionMcpApps`` stand-in that records what it was asked to link."""

    def __init__(self) -> None:
        self.linked: list[list[str]] = []

    def apply(self, history: Sequence[PublicHistoryEntry]) -> list[PublicHistoryEntry]:
        self.linked.append([entry.id for entry in history])
        return [entry.model_copy() for entry in history]


def test_a_live_update_links_only_the_entries_that_changed() -> None:
    """*Prepare*: A Session API path over a projection holding the Turn
        without the tool's result, linked once.
    *Do*: Apply the result and link again; then link a new projection.
    *Assert*: The second link passes only the effect and what follows it to
        the MCP apps and keeps the earlier linked copies; a new projection is
        linked whole.
    """
    # Prepare
    from types import SimpleNamespace

    from vibe.app_server._session_api_path import SessionApiPath
    from vibe.app_server._session_api_session import ApiSessionState
    from vibe.app_server.models import (
        IdleSessionStatus,
        PublicSession,
        PublicSessionState,
    )

    apps = _RecordingApps()
    path = SessionApiPath(cast(Any, SimpleNamespace(_mcp_apps=apps)), cast(Any, None))
    entries = [
        entry
        for entry in _entries(_steps())
        if not isinstance(entry, ToolResultHistoryEntry)
    ]
    state = ApiSessionState(SESSION_ID, [ROOT])
    state.history_projection.reset(entries)
    client = PublicSessionState(
        event_id=0,
        session=PublicSession(
            id=SESSION_ID, status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
    )
    first = path._linked_history(state, client)

    # Do
    state.history_projection.apply(
        HistoryEntryCompletedEvent(entry=_completed_result())
    )
    second = path._linked_history(state, client)
    other = ApiSessionState(SESSION_ID, [ROOT])
    other.history_projection.reset(entries)
    path._linked_history(other, client)

    # Assert
    ids = [entry.id for entry in second]
    position = ids.index("effect-read")
    assert apps.linked[1] == ids[position:]
    assert all(old is new for old, new in zip(first[:position], second, strict=False))
    assert apps.linked[2] == [entry.id for entry in first]


def _client_state(history: list[PublicHistoryEntry]) -> Any:
    """The state a client holds, with ``history``."""
    from vibe.app_server.models import (
        IdleSessionStatus,
        PublicSession,
        PublicSessionState,
    )

    return PublicSessionState(
        event_id=0,
        session=PublicSession(
            id=SESSION_ID, status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
        history=history,
    )


def _settled_question_path() -> tuple[
    Any, Any, PublicCallbackEntry, PublicCallbackEntry
]:
    """A Session API path, a state whose projection holds a settled question,
    the projection's copy of it, and a client copy that differs in its time.
    """
    from types import SimpleNamespace

    from vibe.app_server._session_api_path import SessionApiPath
    from vibe.app_server._session_api_session import ApiSessionState

    output = UserInputCallbackOutput(
        result=UserQuestionResult(answers=[UserAnswer(question="Ship?", answer="Yes")])
    )
    question = UnknownHistoryEntry.model_validate({
        **_base("user-input-1", 0),
        "type": "callback",
        "callbackId": "user-input-1",
        "title": "User input required",
        "detail": {
            "kind": "user_input",
            "request": {
                "questions": [
                    {
                        "question": "Ship?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }
                ]
            },
        },
        "state": {
            "status": "answered",
            "output": output.model_dump(mode="json", by_alias=True),
        },
    })
    state = ApiSessionState(SESSION_ID, [ROOT])
    [projected] = state.history_projection.reset([question])
    assert isinstance(projected, PublicCallbackEntry)
    kept = projected.model_copy(update={"created_at": 1})
    path = SessionApiPath(
        cast(Any, SimpleNamespace(_mcp_apps=_RecordingApps())), cast(Any, None)
    )
    return path, state, projected, kept


def test_a_live_update_keeps_the_client_copy_of_a_settled_callback() -> None:
    """*Prepare*: A Session API path over a projection that holds a settled
        question, and a client that already holds its own settled copy.
    *Do*: Link the history twice, the second time with nothing changed.
    *Assert*: Both links hold the client's copy, so the next update shares it
        with the client instead of comparing the rest of the history again.
    """
    # Prepare
    path, state, _projected, kept = _settled_question_path()
    client = _client_state([kept])

    # Do
    first = path._linked_history(state, client)
    second = path._linked_history(state, client)

    # Assert
    assert first[0] is kept
    assert second[0] is kept


def test_a_resolution_after_its_entry_links_the_client_copy_next_time() -> None:
    """*Prepare*: A Session API path that linked a settled question while the
        client still held the question open.
    *Do*: The resolution reaches the client with its own copy; link again with
        nothing changed in the projection.
    *Assert*: The first link holds the projection's copy, the second the
        client's, so later updates share it with the client.
    """
    # Prepare
    path, state, projected, kept = _settled_question_path()
    open_client = _client_state([
        kept.model_copy(
            update={"generation_status": PublicEntryGenerationStatus.IN_PROGRESS}
        )
    ])
    first = path._linked_history(state, open_client)

    # Do
    path._question_resolved = True
    second = path._linked_history(state, _client_state([kept]))

    # Assert
    assert first[0] == projected
    assert first[0] is not kept
    assert second[0] is kept
