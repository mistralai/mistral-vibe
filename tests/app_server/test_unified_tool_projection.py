from __future__ import annotations

from copy import deepcopy

import pytest

from vibe.app_server._unified_tool_projection import project_unified_history_entry
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    CompletedEffectState,
    EffectResultDisplay,
    FailedEffectState,
    FileEditEffectBatchInput,
    FileEditEffectDetail,
    FileEditEffectOutput,
    FileReadEffectDetail,
    FileReadEffectOutput,
    FileSearchEffectDetail,
    FileSearchEffectOutput,
    FileWriteEffectDetail,
    FileWriteEffectOutput,
    GenericEffectDetail,
    ProcessEffectDetail,
    PublicCallbackEntry,
    PublicEffectEntry,
    ShellEffectDetail,
    ShellEffectOutput,
    SkillEffectDetail,
    TodoEffectDetail,
    TodoEffectItem,
    TodoEffectOutput,
    UserQuestionEffectDetail,
    UserQuestionResult,
    WebFetchEffectDetail,
    WebFetchEffectOutput,
    WebSearchEffectDetail,
    WebSearchEffectOutput,
    validate_history_entry,
)


def test_projects_unified_read_file_with_nullable_limit() -> None:
    entry = _effect(
        "file_system.read_file",
        {"path": "src/main.py", "offset": 1, "limit": None},
        result={
            "structured_content": {
                "path": "/workspace/src/main.py",
                "content": "second\nthird\n",
                "file_size_bytes": 30,
                "returned_bytes": 13,
                "offset": 1,
                "lines_read": 2,
                "was_truncated": False,
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert projected.id == entry.id
    assert isinstance(projected.detail, FileReadEffectDetail)
    assert projected.detail.tool_name == "file_system.read_file"
    assert projected.detail.input is not None
    assert projected.detail.input.file_path == "src/main.py"
    assert projected.detail.input.offset == 2
    assert projected.detail.input.limit is None
    assert isinstance(projected.state, CompletedEffectState)
    assert FileReadEffectOutput.model_validate(projected.state.output) == (
        FileReadEffectOutput(
            file_path="/workspace/src/main.py",
            content="second\nthird\n",
            num_lines=2,
            start_line=2,
            requested_offset=2,
            requested_limit=None,
            was_truncated=False,
        )
    )


@pytest.mark.parametrize(
    ("source_offset", "display_offset", "resolved_offset", "start_line"),
    [(0, None, 0, 1), (-1, -1, 8, 9)],
)
def test_projects_unified_read_offsets(
    source_offset: int,
    display_offset: int | None,
    resolved_offset: int,
    start_line: int,
) -> None:
    entry = _effect(
        "read_file",
        {"path": "README.md", "offset": source_offset},
        result={
            "structured_content": {
                "path": "/workspace/README.md",
                "content": "line\n",
                "offset": resolved_offset,
                "lines_read": 1,
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, FileReadEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.offset == display_offset
    assert isinstance(projected.state, CompletedEffectState)
    output = FileReadEffectOutput.model_validate(projected.state.output)
    assert output.start_line == start_line
    assert output.requested_offset == display_offset


def test_projects_unified_write_file_with_retained_call_content() -> None:
    entry = _effect(
        "write_file",
        {"path": "notes.txt", "content": "hello\n"},
        result={
            "structured_content": {
                "path": "/workspace/notes.txt",
                "bytes_written": 6,
                "file_existed": False,
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, FileWriteEffectDetail)
    assert isinstance(projected.state, CompletedEffectState)
    assert FileWriteEffectOutput.model_validate(projected.state.output) == (
        FileWriteEffectOutput(file_path="/workspace/notes.txt", content="hello\n")
    )


def test_projects_unified_batched_search_replace_and_occurrences() -> None:
    entry = _effect(
        "file_system.search_replace",
        {
            "file_path": "notes.txt",
            "content": [
                {"old_str": "alpha", "new_str": "beta"},
                {"old_str": "one", "new_str": "two", "replace_all": True},
            ],
        },
        result={
            "structured_content": {
                "file": "/workspace/notes.txt",
                "lines_changed": 3,
                "warnings": ["review generated content"],
            },
            "_meta": {
                "mistralai.vibe.sdk.search_replace": {
                    "blocks": [
                        {
                            "old_start_line": 2,
                            "new_start_line": 2,
                            "old_lines": ["alpha"],
                            "new_lines": ["beta"],
                        },
                        {
                            "old_start_line": 5,
                            "new_start_line": 5,
                            "old_lines": ["one\n"],
                            "new_lines": ["two\n"],
                        },
                    ]
                }
            },
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, FileEditEffectDetail)
    assert isinstance(projected.detail.input, FileEditEffectBatchInput)
    assert [change.old_string for change in projected.detail.input.changes] == [
        "alpha",
        "one",
    ]
    assert projected.detail.input.changes[1].replace_all
    assert isinstance(projected.state, CompletedEffectState)
    output = FileEditEffectOutput.model_validate(projected.state.output)
    assert output.old_string is None
    assert output.new_string is None
    assert [
        (item.start_line, item.old_text, item.new_text) for item in output.occurrences
    ] == [(2, "alpha", "beta"), (5, "one\n", "two\n")]
    assert projected.state.display.warnings == ["review generated content"]


def test_projects_unified_shell_result_from_structured_transcript() -> None:
    entry = _effect(
        "bash",
        {"command": "printf out; printf err >&2"},
        result={
            "structured_content": {
                "command": "printf out; printf err >&2",
                "stdout": "out",
                "stderr": "err",
                "output": "outerr",
                "returncode": 0,
                "was_truncated": True,
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, ShellEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.command == "printf out; printf err >&2"
    assert projected.detail.display.status_text == "Running command"
    assert isinstance(projected.state, CompletedEffectState)
    assert ShellEffectOutput.model_validate(
        projected.state.output
    ) == ShellEffectOutput(stdout="out", stderr="err", output="outerr", truncated=True)
    assert projected.state.output_text == "outerr"


def test_shell_projection_preserves_the_auto_approved_note() -> None:
    """*Prepare*: A completed bash effect whose incoming display carries a
    smart-approve auto-approved note.
    *Do*: Project it through the Vibe-owned semantic adapter.
    *Assert*: The rebuilt shell display keeps the note so the TUI can render it.
    """
    note = "Auto-approved: read-only command (ls)"
    entry = _effect(
        "bash",
        {"command": "ls"},
        result={
            "structured_content": {
                "command": "ls",
                "stdout": "file.txt\n",
                "stderr": "",
                "output": "file.txt\n",
                "returncode": 0,
                "was_truncated": False,
            }
        },
        approval_note=note,
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.approval_note == note
    # It is not a warning: the call was authorised and succeeded.
    assert projected.state.display.warnings == []


def test_shell_projection_keeps_a_pre_split_note_stored_as_a_warning() -> None:
    """*Prepare*: A completed bash effect from a session written before the
    approval note had its own field, so the note sits in ``warnings``.
    *Do*: Project it through the Vibe-owned semantic adapter.
    *Assert*: The note survives replay instead of being dropped.
    """
    note = "Auto-approved: read-only command (ls)"
    entry = _effect(
        "bash",
        {"command": "ls"},
        result={
            "structured_content": {
                "command": "ls",
                "stdout": "file.txt\n",
                "stderr": "",
                "output": "file.txt\n",
                "returncode": 0,
                "was_truncated": False,
            }
        },
        warnings=[note],
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.warnings == [note]


def test_projects_unified_skill_without_exposing_the_runtime_result_envelope() -> None:
    """*Prepare*: A generic Unified skill call whose text result contains the loaded body.
    *Do*: Project it through the Vibe-owned semantic adapter.
    *Assert*: The skill keeps its rich detail and text while dropping the raw envelope.
    """
    body = '<skill_content name="code-review">\nReview twice.\n</skill_content>'
    entry = _effect(
        "skill.read",
        {"name": "code-review"},
        result={"structured_content": body},
        output_text=body,
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, SkillEffectDetail)
    assert projected.detail.tool_name == "skill"
    assert projected.detail.input is not None
    assert projected.detail.input.name == "code-review"
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.output is None
    assert projected.state.output_text == body
    assert projected.state.display.message == "skill: code-review"


@pytest.mark.parametrize(
    ("name", "input_value", "result"),
    [
        (
            "connector.github.search",
            {"q": "Harness"},
            {"structured_content": {"ok": True}},
        ),
        ("read_file", {"offset": 0}, {"structured_content": {"path": "x"}}),
        (
            "file_system.read_file",
            {"path": "README.md"},
            {"structured_content": {"path": "README.md", "content": 42}},
        ),
    ],
)
def test_unknown_or_malformed_unified_tools_remain_exactly_generic(
    name: str, input_value: object, result: object
) -> None:
    entry = _effect(name, input_value, result=result)
    original = entry.model_copy(deep=True)

    projected = project_unified_history_entry(entry)

    assert projected == original
    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, GenericEffectDetail)


@pytest.mark.parametrize(
    ("name", "verb", "message", "settled_verb"),
    [("subagent.wait", "Waiting", "for a subagent", "Waited")],
)
def test_humanizes_namespaced_builtin_labels(
    name: str, verb: str, message: str, settled_verb: str
) -> None:
    # The Harness settles a generic tool with its raw key as the result message.
    entry = _effect(name, {}, result={"structured_content": {"ok": True}})
    entry = entry.model_copy(
        update={
            "state": entry.state.model_copy(
                update={"display": EffectResultDisplay(success=True, message=name)}
            )
        }
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    # Identity is untouched; only the human-facing presentation changes.
    assert isinstance(projected.detail, GenericEffectDetail)
    assert projected.detail.tool_name == name
    assert projected.detail.display.verb == verb
    assert projected.detail.display.message == message
    assert projected.detail.display.settled_verb == settled_verb
    assert name not in projected.detail.display.summary.split()
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.message == message
    assert projected.state.display.verb == settled_verb


def test_projects_process_start_with_command_in_display() -> None:
    entry = _effect(
        "process.start",
        {"command": "sleep 30"},
        result={
            "structured_content": {
                "processId": "process-abc12345-abcdef",
                "status": "running",
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, ProcessEffectDetail)
    assert projected.detail.tool_name == "process.start"
    assert projected.detail.display.verb == "Starting"
    assert projected.detail.display.message == "sleep 30"
    assert projected.detail.display.settled_verb == "Started"
    assert projected.detail.display.settled_message == "sleep 30"
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.verb == "Started"
    assert projected.state.display.message == "sleep 30"
    assert projected.state.display.suffix == "(running)"


def test_self_sleep_label_renders_the_verb_alone() -> None:
    # The Harness stamps the raw tool key as the settled result message.
    entry = _effect("self.sleep", {"seconds": 1}, result={"structured_content": {}})
    entry = entry.model_copy(
        update={
            "state": entry.state.model_copy(
                update={
                    "display": EffectResultDisplay(success=True, message="self.sleep")
                }
            )
        }
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, GenericEffectDetail)
    assert projected.detail.display.verb == "Sleeping"
    # Empty string (not None) keeps the header as the verb alone rather than
    # falling back to the summary or leaking the raw "self.sleep" key.
    assert projected.detail.display.message == ""
    assert projected.detail.display.settled_message == ""
    assert projected.detail.display.summary == "Sleeping"
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.verb == "Slept"
    assert projected.state.display.message == ""


@pytest.mark.parametrize(
    ("name", "verb", "message", "settled_verb"),
    [
        ("connector_github_app.get_file_contents", "Reading", "file contents", "Read"),
        ("connector_github_app.search_code", "Searching", "code", "Searched"),
        (
            "connector_github_app.create_pull_request",
            "Creating",
            "a pull request",
            "Created",
        ),
        (
            "connector_github_app.merge_pull_request",
            "Merging",
            "a pull request",
            "Merged",
        ),
        (
            "connector_google_calendar.list_events",
            "Listing",
            "calendar events",
            "Listed",
        ),
        (
            "connector_google_drive_mcp.read_file_content",
            "Reading",
            "a Drive file",
            "Read",
        ),
        ("connector_mistral_ai.list_skills", "Listing", "skills", "Listed"),
        ("connector_slack.slack_send_message", "Sending", "a Slack message", "Sent"),
        ("connector_web_search.web_search", "Searching", "the web", "Searched"),
    ],
)
def test_humanizes_connector_tool_labels(
    name: str, verb: str, message: str, settled_verb: str
) -> None:
    entry = _effect(name, {}, result={"structured_content": {"ok": True}})
    entry = entry.model_copy(
        update={
            "state": entry.state.model_copy(
                update={"display": EffectResultDisplay(success=True, message=name)}
            )
        }
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, GenericEffectDetail)
    assert projected.detail.tool_name == name
    assert name not in projected.detail.display.summary.split()
    assert projected.detail.display.verb == verb
    assert projected.detail.display.message == message
    assert projected.detail.display.settled_verb == settled_verb
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.verb == settled_verb
    assert projected.state.display.message == message


def test_humanizes_labeled_builtin_through_approval_callback() -> None:
    raw = {
        "type": "callback",
        "id": "approval-action-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "in_progress",
        "relatedEntryId": "effect-action-1",
        "callbackId": "approval-action-1",
        "title": "Approve subagent.wait",
        "detail": {
            "kind": "approval",
            "effect": _generic_detail("subagent.wait", {}),
            "requiredPermissions": [],
            "choices": ["approve", "deny"],
            "relatedEntryId": "effect-action-1",
        },
        "state": {"status": "open"},
    }
    callback = validate_history_entry(raw)
    assert isinstance(callback, PublicCallbackEntry)

    projected = project_unified_history_entry(callback)

    assert isinstance(projected, PublicCallbackEntry)
    assert isinstance(projected.detail, ApprovalCallbackDetail)
    effect = projected.detail.effect
    assert isinstance(effect, GenericEffectDetail)
    assert effect.tool_name == "subagent.wait"
    assert effect.display.verb == "Waiting"
    assert effect.display.message == "for a subagent"


def test_failed_recognized_tool_keeps_semantic_call_detail() -> None:
    entry = _effect(
        "file_system.read_file",
        {"path": "missing.txt", "offset": 0, "limit": 20},
        error="File not found",
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, FileReadEffectDetail)
    assert isinstance(projected.state, FailedEffectState)
    assert projected.state.error.message == "File not found"


def test_projects_unified_ask_user_question_result() -> None:
    questions_input = {
        "questions": [
            {
                "question": "Which visual direction do you prefer?",
                "header": "",
                "options": [
                    {"label": "Distinct background", "description": ""},
                    {"label": "Inline highlight", "description": ""},
                ],
            }
        ]
    }
    result = {
        "structured_content": {
            "answers": [
                {
                    "question": "Which visual direction do you prefer?",
                    "answer": "Distinct background",
                    "isOther": False,
                }
            ],
            "cancelled": False,
        }
    }
    entry = _effect("ui.ask_user_question", questions_input, result=result)

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, UserQuestionEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.questions[0].question == (
        "Which visual direction do you prefer?"
    )
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.verb == "Answered"
    assert projected.state.display.message == (
        '"Which visual direction do you prefer?" → Distinct background'
    )
    result = UserQuestionResult.model_validate(projected.state.output)
    assert result.answers[0].answer == "Distinct background"
    assert result.cancelled is False


def test_projects_unified_ask_user_question_cancelled() -> None:
    questions_input = {
        "questions": [
            {
                "question": "Pick one",
                "header": "",
                "options": [
                    {"label": "A", "description": ""},
                    {"label": "B", "description": ""},
                ],
            }
        ]
    }
    result = {"structured_content": {"answers": [], "cancelled": True}}
    entry = _effect("ui.ask_user_question", questions_input, result=result)

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, UserQuestionEffectDetail)
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.verb == "Cancelled"
    assert projected.state.display.message == "by user"


def test_projects_unified_ask_user_question_multi_question() -> None:
    questions_input = {
        "questions": [
            {
                "question": "Q1",
                "header": "",
                "options": [
                    {"label": "A", "description": ""},
                    {"label": "B", "description": ""},
                ],
            },
            {
                "question": "Q2",
                "header": "",
                "options": [
                    {"label": "C", "description": ""},
                    {"label": "D", "description": ""},
                ],
            },
        ]
    }
    result = {
        "structured_content": {
            "answers": [
                {"question": "Q1", "answer": "A", "isOther": False},
                {"question": "Q2", "answer": "D", "isOther": False},
            ],
            "cancelled": False,
        }
    }
    entry = _effect("ui.ask_user_question", questions_input, result=result)

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, UserQuestionEffectDetail)
    assert projected.detail.display.message == "2 questions"
    assert isinstance(projected.state, CompletedEffectState)
    assert projected.state.display.verb == "Answered"
    assert projected.state.display.message == '"Q1" → A · "Q2" → D'


def test_projects_approval_callback_with_the_same_semantic_call_detail() -> None:
    raw = {
        "type": "callback",
        "id": "approval-action-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "in_progress",
        "relatedEntryId": "effect-action-1",
        "callbackId": "approval-action-1",
        "title": "Approve file_system.write_file",
        "detail": {
            "kind": "approval",
            "effect": _generic_detail(
                "file_system.write_file", {"path": "notes.txt", "content": "hello"}
            ),
            "requiredPermissions": [],
            "choices": ["approve", "deny"],
            "relatedEntryId": "effect-action-1",
        },
        "state": {"status": "open"},
    }
    callback = validate_history_entry(raw)
    assert isinstance(callback, PublicCallbackEntry)

    projected = project_unified_history_entry(callback)

    assert isinstance(projected, PublicCallbackEntry)
    assert isinstance(projected.detail, ApprovalCallbackDetail)
    assert isinstance(projected.detail.effect, FileWriteEffectDetail)
    assert projected.detail.effect.input is not None
    assert projected.detail.effect.input.file_path == "notes.txt"
    assert projected.related_entry_id == "effect-action-1"


def test_projects_unified_web_search_result() -> None:
    entry = _effect(
        "web_search",
        {"query": "mistral ai"},
        result={
            "structured_content": {
                "query": "mistral ai",
                "answer": "Mistral AI is a Paris-based AI company.",
                "sources": [
                    {"title": "Mistral AI", "url": "https://mistral.ai"},
                    {"title": "Wikipedia", "url": "https://wikipedia.org"},
                ],
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, WebSearchEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.query == "mistral ai"
    assert projected.detail.display.verb == "Searching"
    assert "mistral ai" in projected.detail.display.summary
    assert isinstance(projected.state, CompletedEffectState)
    output = WebSearchEffectOutput.model_validate(projected.state.output)
    assert output.query == "mistral ai"
    assert output.answer == "Mistral AI is a Paris-based AI company."
    assert len(output.sources) == 2
    assert output.sources[0].title == "Mistral AI"
    assert projected.state.display.verb == "Searched"
    assert "2 sources" in projected.state.display.message


def test_projects_unified_web_fetch_result() -> None:
    entry = _effect(
        "web_fetch",
        {"url": "https://example.com/page", "timeout": 30},
        result={
            "structured_content": {
                "url": "https://example.com/page",
                "content": "Hello world",
                "content_type": "text/html; charset=utf-8",
                "was_truncated": True,
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, WebFetchEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.url == "https://example.com/page"
    assert projected.detail.input.timeout == 30
    assert "example.com" in projected.detail.display.summary
    assert "timeout 30s" in projected.detail.display.summary
    assert isinstance(projected.state, CompletedEffectState)
    output = WebFetchEffectOutput.model_validate(projected.state.output)
    assert output.url == "https://example.com/page"
    assert output.content == "Hello world"
    assert output.content_type == "text/html; charset=utf-8"
    assert output.was_truncated is True
    assert projected.state.display.verb == "Fetched"
    assert "11 chars" in projected.state.display.message
    assert "text/html" in projected.state.display.message
    assert projected.state.display.suffix == "(truncated)"


def test_projects_unified_grep_result() -> None:
    entry = _effect(
        "grep",
        {"pattern": "TODO", "path": "src/", "max_matches": 50},
        result={
            "structured_content": {
                "matches": "src/main.py:10:TODO fix\nsrc/util.py:5:TODO refactor",
                "match_count": 2,
                "was_truncated": False,
                "parsed_matches": [
                    {"path": "src/main.py", "line": 10},
                    {"path": "src/util.py", "line": 5},
                ],
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, FileSearchEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.pattern == "TODO"
    assert projected.detail.input.path == "src/"
    assert projected.detail.input.max_matches == 50
    assert "in src/" in (projected.detail.display.message or "")
    assert "max 50 matches" in (projected.detail.display.message or "")
    assert isinstance(projected.state, CompletedEffectState)
    output = FileSearchEffectOutput.model_validate(projected.state.output)
    assert output.match_count == 2
    assert output.parsed_matches[0].path == "src/main.py"
    assert output.parsed_matches[0].line == 10
    assert projected.state.display.verb == "Searched"
    assert "TODO" in projected.state.display.message
    assert "2 matches" in projected.state.display.message


def test_projects_unified_todo_result() -> None:
    entry = _effect(
        "todo",
        {"action": "write", "todos": [{"id": "1", "content": "Task A"}]},
        result={
            "structured_content": {
                "verb": "Updated",
                "todos": [{"id": "1", "content": "Task A", "status": "pending"}],
                "total_count": 1,
                "message": "Updated 1 todos",
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, TodoEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.action == "write"
    assert projected.detail.input.todos is not None
    assert len(projected.detail.input.todos) == 1
    assert projected.detail.input.todos[0].id == "1"
    assert projected.detail.input.todos[0].content == "Task A"
    assert projected.detail.display.verb == "Updating"
    assert "1 todos" in projected.detail.display.summary
    assert isinstance(projected.state, CompletedEffectState)
    output = TodoEffectOutput.model_validate(projected.state.output)
    assert output.todos == [TodoEffectItem(id="1", content="Task A")]
    assert projected.state.display.verb == "Updated"
    assert projected.state.display.message == "1 todos"


def test_projects_unified_todo_read_action() -> None:
    entry = _effect(
        "todo",
        {"action": "read"},
        result={
            "structured_content": {
                "verb": "Retrieved",
                "todos": [
                    {"id": "1", "content": "Task A", "status": "completed"},
                    {"id": "2", "content": "Task B", "status": "in_progress"},
                ],
                "total_count": 2,
                "message": "Retrieved 2 todos",
            }
        },
    )

    projected = project_unified_history_entry(entry)

    assert isinstance(projected, PublicEffectEntry)
    assert isinstance(projected.detail, TodoEffectDetail)
    assert projected.detail.input is not None
    assert projected.detail.input.action == "read"
    assert projected.detail.input.todos is None
    assert projected.detail.display.verb == "Retrieving"
    assert projected.detail.display.message == "todos"
    assert isinstance(projected.state, CompletedEffectState)
    output = TodoEffectOutput.model_validate(projected.state.output)
    assert len(output.todos) == 2
    assert output.todos[0].id == "1"
    assert output.todos[0].content == "Task A"
    assert projected.state.display.verb == "Retrieved"
    assert projected.state.display.message == "2 todos"


def _effect(
    name: str,
    input_value: object,
    *,
    result: object | None = None,
    error: str | None = None,
    output_text: str = "",
    warnings: list[str] | None = None,
    approval_note: str | None = None,
) -> PublicEffectEntry:
    completed_display: dict[str, object] = {
        "success": True,
        "message": f"{name} completed",
    }
    if warnings is not None:
        completed_display["warnings"] = warnings
    if approval_note is not None:
        completed_display["approvalNote"] = approval_note
    raw = {
        "type": "effect",
        "id": "effect-action-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 2,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "title": name,
        "detail": _generic_detail(name, input_value),
        "state": (
            {
                "status": "failed",
                "error": {"message": error},
                "display": {"success": False, "message": error},
            }
            if error is not None
            else {
                "status": "completed",
                "output": deepcopy(result),
                "outputText": output_text,
                "display": completed_display,
            }
        ),
    }
    entry = validate_history_entry(raw)
    assert isinstance(entry, PublicEffectEntry)
    return entry


def _generic_detail(name: str, input_value: object) -> dict[str, object]:
    return {
        "kind": "tool",
        "toolName": name,
        "input": deepcopy(input_value),
        "display": {
            "summary": name,
            "verb": "Running",
            "message": name,
            "settledVerb": "Ran",
            "settledMessage": name,
            "statusText": f"Running {name}",
        },
    }
