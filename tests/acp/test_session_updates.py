from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ContentToolCallContent,
    FileEditToolCallContent,
    SessionInfoUpdate,
    TextContentBlock as AcpTextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)

from vibe.acp.session_updates import replay_history_entry, session_updates_for_event
from vibe.acp.user_display_content import USER_DISPLAY_CONTENT_META_KEY
from vibe.app_server.events import (
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionUpdated,
)
from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    FileEditEffectDetail,
    FileEditEffectInput,
    FileReadEffectDetail,
    FileReadEffectInput,
    FileReadEffectOutput,
    FileSearchEffectDetail,
    FileSearchEffectInput,
    FileSearchEffectMatch,
    FileSearchEffectOutput,
    IdleSessionStatus,
    JsonPatchOperation,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    PublicNoticeEntry,
    PublicReasoningEntry,
    PublicSession,
    RunningEffectState,
    SessionTitleUpdatedNoticeDetail,
    SkillEffectDetail,
    SkillEffectInput,
    SkillEffectOutput,
    SubagentEffectDetail,
    SubagentEffectInput,
    SubagentEffectOutput,
    TextContentBlock,
    TodoEffectDetail,
    TodoEffectInput,
    UserDisplayContent,
    WebFetchEffectDetail,
    WebFetchEffectInput,
    WebFetchEffectOutput,
    WebSearchEffectDetail,
    WebSearchEffectInput,
    WebSearchEffectOutput,
    WebSearchEffectSource,
)


class _EntryFields(TypedDict):
    id: str
    session_id: str
    turn_id: str
    created_at: int
    updated_at: int
    generation_status: PublicEntryGenerationStatus


def _entry_fields(entry_id: str, status: PublicEntryGenerationStatus) -> _EntryFields:
    return {
        "id": entry_id,
        "session_id": "session-1",
        "turn_id": "turn-1",
        "created_at": 1_000,
        "updated_at": 2_000,
        "generation_status": status,
    }


def _display(summary: str = "Running tool") -> EffectCallDisplay:
    return EffectCallDisplay(
        summary=summary, content="Call details", status_text="Running"
    )


def _session(title: str | None, updated_at: int) -> PublicSession:
    return PublicSession(
        id="session-1",
        title=title,
        status=IdleSessionStatus(),
        created_at=1_000,
        updated_at=updated_at,
    )


def test_replays_public_messages_and_reasoning_with_stable_entry_ids() -> None:
    user = PublicMessageEntry(
        **_entry_fields("message-user", PublicEntryGenerationStatus.COMPLETED),
        role="user",
        content=[TextContentBlock(text="hello")],
    )
    assistant = PublicMessageEntry(
        **_entry_fields("message-agent", PublicEntryGenerationStatus.COMPLETED),
        role="assistant",
        content=[TextContentBlock(text="hi")],
    )
    reasoning = PublicReasoningEntry(
        **_entry_fields("reasoning-1", PublicEntryGenerationStatus.COMPLETED),
        text="thinking",
    )

    user_update = replay_history_entry(user)[0]
    assistant_update = replay_history_entry(assistant)[0]
    reasoning_update = replay_history_entry(reasoning)[0]

    assert isinstance(user_update, UserMessageChunk)
    assert user_update.message_id == "message-user"
    assert isinstance(assistant_update, AgentMessageChunk)
    assert assistant_update.message_id == "message-agent"
    assert isinstance(reasoning_update, AgentThoughtChunk)
    assert reasoning_update.message_id == "reasoning-1"


def test_user_message_stamps_display_snapshot_on_each_chunk() -> None:
    display = UserDisplayContent(
        version="1.0.0",
        host="vibe-vscode",
        content=[
            {"type": "text", "text": "What is this file?"},
            {"type": "workspace_mention", "kind": "file", "name": "utils.ts"},
        ],
    )
    user = PublicMessageEntry(
        **_entry_fields("message-user", PublicEntryGenerationStatus.COMPLETED),
        role="user",
        content=[
            TextContentBlock(text="What is this file?"),
            TextContentBlock(text="utils.ts contents"),
        ],
        user_display_content=display,
    )

    updates = replay_history_entry(user)

    # One chunk per content block; each carries the whole-message snapshot, which
    # the webview treats as a replace so the bubble is not duplicated.
    assert len(updates) == 2
    expected_meta = display.model_dump(mode="json", by_alias=True)
    for chunk in updates:
        assert isinstance(chunk, UserMessageChunk)
        assert chunk.field_meta is not None
        assert chunk.field_meta[USER_DISPLAY_CONTENT_META_KEY] == expected_meta


def test_live_message_and_reasoning_updates_emit_only_appended_text() -> None:
    previous_message = PublicMessageEntry(
        **_entry_fields("message-1", PublicEntryGenerationStatus.IN_PROGRESS),
        role="assistant",
        content=[TextContentBlock(text="hell")],
    )
    message = previous_message.model_copy(
        update={"content": [TextContentBlock(text="hello")], "updated_at": 3_000}
    )
    previous_reasoning = PublicReasoningEntry(
        **_entry_fields("reasoning-1", PublicEntryGenerationStatus.IN_PROGRESS),
        text="think",
    )
    reasoning = previous_reasoning.model_copy(
        update={"text": "thinking", "updated_at": 3_000}
    )

    message_updates = session_updates_for_event(
        HistoryEntryUpdated(
            previous=previous_message,
            entry=message,
            patch=[JsonPatchOperation(op="append", path="/content/0/text", value="o")],
        )
    )
    reasoning_updates = session_updates_for_event(
        HistoryEntryUpdated(
            previous=previous_reasoning,
            entry=reasoning,
            patch=[JsonPatchOperation(op="append", path="/text", value="ing")],
        )
    )

    message_update = message_updates[0]
    reasoning_update = reasoning_updates[0]
    assert isinstance(message_update, AgentMessageChunk)
    assert isinstance(message_update.content, AcpTextContentBlock)
    assert message_update.content.text == "o"
    assert isinstance(reasoning_update, AgentThoughtChunk)
    assert isinstance(reasoning_update.content, AcpTextContentBlock)
    assert reasoning_update.content.text == "ing"


def test_effect_projection_uses_semantic_kind_for_arbitrary_tool_names() -> None:
    running = PublicEffectEntry(
        **_entry_fields("effect-1", PublicEntryGenerationStatus.IN_PROGRESS),
        title="Read file",
        detail=FileReadEffectDetail(
            tool_name="plugin__read_anything",
            input=FileReadEffectInput(file_path="src/main.py", offset=10, limit=20),
            display=_display("Reading src/main.py"),
        ),
        state=RunningEffectState(output_text="first"),
    )
    completed = running.model_copy(
        update={
            "generation_status": PublicEntryGenerationStatus.COMPLETED,
            "updated_at": 3_000,
            "state": CompletedEffectState(
                output=FileReadEffectOutput(
                    file_path="src/main.py",
                    content="contents",
                    num_lines=2,
                    start_line=10,
                    requested_offset=10,
                    requested_limit=20,
                    total_lines=11,
                ).model_dump(mode="json", by_alias=True),
                output_text="first second",
                duration_ms=12,
                display=EffectResultDisplay(success=True, message="Read 2 lines"),
            ),
        }
    )

    start = replay_history_entry(running)[0]
    progress_updates = session_updates_for_event(
        HistoryEntryUpdated(
            previous=running,
            entry=completed,
            patch=[JsonPatchOperation(op="replace", path="/state", value=None)],
        )
    )
    progress = progress_updates[0]

    assert isinstance(start, ToolCallStart)
    assert start.tool_call_id == "effect-1"
    assert start.kind == "read"
    assert start.raw_input == {"filePath": "src/main.py", "offset": 10, "limit": 20}
    assert start.locations is not None
    assert start.locations[0].field_meta == {
        "type": "file_range",
        "offset": 10,
        "limit": 20,
    }

    assert isinstance(progress, ToolCallProgress)
    assert progress.tool_call_id == "effect-1"
    assert progress.status == "completed"
    assert progress.raw_output == {
        "filePath": "src/main.py",
        "content": "contents",
        "numLines": 2,
        "startLine": 10,
        "requestedOffset": 10,
        "requestedLimit": 20,
        "totalLines": 11,
        "wasTruncated": False,
    }
    assert progress.field_meta == {
        "tool_name": "plugin__read_anything",
        "effect_kind": "file_read",
    }
    assert progress.content is not None
    texts = [
        item.content.text
        for item in progress.content
        if isinstance(item, ContentToolCallContent)
        and isinstance(item.content, AcpTextContentBlock)
    ]
    assert texts == [" second", "Read 2 lines"]


def test_file_effects_and_todos_keep_rich_acp_semantics() -> None:
    edit = PublicEffectEntry(
        **_entry_fields("edit-effect", PublicEntryGenerationStatus.COMPLETED),
        title="Edit",
        detail=FileEditEffectDetail(
            tool_name="arbitrary_edit_provider",
            input=FileEditEffectInput(
                file_path="app.py", old_string="old", new_string="new"
            ),
            display=_display("Editing app.py"),
        ),
        state=CompletedEffectState(
            output={"file": "app.py", "oldString": "old", "newString": "new"},
            display=EffectResultDisplay(success=True, message="Edited app.py"),
        ),
    )
    todo = PublicEffectEntry(
        **_entry_fields("todo-effect", PublicEntryGenerationStatus.COMPLETED),
        title="Todos",
        detail=TodoEffectDetail(
            tool_name="any_todo_tool",
            input=TodoEffectInput(action="replace"),
            display=_display("Updating todos"),
        ),
        state=CompletedEffectState(
            output={
                "todos": [
                    {
                        "id": "implement",
                        "content": "Implement",
                        "status": "in_progress",
                        "priority": "high",
                    },
                    {
                        "id": "old",
                        "content": "Old",
                        "status": "cancelled",
                        "priority": "low",
                    },
                ]
            },
            display=EffectResultDisplay(success=True, message="Updated todos"),
        ),
    )

    edit_update = replay_history_entry(edit)[0]
    todo_updates = replay_history_entry(todo)

    assert isinstance(edit_update, ToolCallStart)
    assert edit_update.content is not None
    assert isinstance(edit_update.content[0], FileEditToolCallContent)
    assert edit_update.content[0].path == "app.py"
    assert edit_update.locations is not None
    assert edit_update.locations[0].path == str(Path("app.py").resolve())

    assert isinstance(todo_updates[0], ToolCallStart)
    assert todo_updates[0].tool_call_id == "todo-effect"
    assert isinstance(todo_updates[1], AgentPlanUpdate)
    assert todo_updates[1].field_meta == {"effect_entry_id": "todo-effect"}
    assert [entry.content for entry in todo_updates[1].entries] == ["Implement"]


def test_file_search_projects_query_and_result_match_locations() -> None:
    running = PublicEffectEntry(
        **_entry_fields("search-effect", PublicEntryGenerationStatus.IN_PROGRESS),
        title="Search",
        detail=FileSearchEffectDetail(
            tool_name="extension_search",
            input=FileSearchEffectInput(pattern="TODO", path="src"),
            display=_display("Searching"),
        ),
        state=RunningEffectState(),
    )
    completed = running.model_copy(
        update={
            "generation_status": PublicEntryGenerationStatus.COMPLETED,
            "state": CompletedEffectState(
                output=FileSearchEffectOutput(
                    matches="src/a.py:10:TODO\nsrc/b.py:20:TODO",
                    match_count=2,
                    was_truncated=False,
                    parsed_matches=[
                        FileSearchEffectMatch(
                            path=str(Path("src/a.py").resolve()), line=10
                        ),
                        FileSearchEffectMatch(
                            path=str(Path("src/b.py").resolve()), line=20
                        ),
                    ],
                ).model_dump(mode="json", by_alias=True),
                display=EffectResultDisplay(success=True, message="Found 2 matches"),
            ),
        }
    )

    call = replay_history_entry(running)[0]
    result = replay_history_entry(completed)[0]

    assert isinstance(call, ToolCallStart)
    assert call.kind == "search"
    assert call.locations is None
    assert call.field_meta == {
        "tool_name": "extension_search",
        "effect_kind": "file_search",
        "query": "TODO",
        "search_path": str(Path("src").resolve()),
    }
    assert isinstance(result, ToolCallStart)
    assert result.locations is not None
    assert [(location.path, location.line) for location in result.locations] == [
        (str(Path("src/a.py").resolve()), 10),
        (str(Path("src/b.py").resolve()), 20),
    ]


def test_file_read_locations_describe_the_actual_range() -> None:
    whole_file = PublicEffectEntry(
        **_entry_fields("read-whole", PublicEntryGenerationStatus.IN_PROGRESS),
        title="Read",
        detail=FileReadEffectDetail(
            tool_name="extension_read",
            input=FileReadEffectInput(file_path="README.md"),
            display=_display("Reading"),
        ),
        state=RunningEffectState(),
    )
    bounded = whole_file.model_copy(
        update={
            "id": "read-bounded",
            "detail": whole_file.detail.model_copy(
                update={
                    "input": FileReadEffectInput(
                        file_path="README.md", offset=10, limit=50
                    )
                }
            ),
        }
    )
    truncated = bounded.model_copy(
        update={
            "generation_status": PublicEntryGenerationStatus.COMPLETED,
            "state": CompletedEffectState(
                output=FileReadEffectOutput(
                    file_path="README.md",
                    content="content",
                    num_lines=3,
                    start_line=10,
                    requested_offset=10,
                    requested_limit=50,
                    total_lines=12,
                    was_truncated=True,
                ).model_dump(mode="json", by_alias=True),
                display=EffectResultDisplay(success=True, message="Read 3 lines"),
            ),
        }
    )

    whole_location = replay_history_entry(whole_file)[0].locations
    bounded_location = replay_history_entry(bounded)[0].locations
    result_location = replay_history_entry(truncated)[0].locations

    assert whole_location is not None
    assert whole_location[0].line is None
    assert whole_location[0].field_meta == {"type": "file"}
    assert bounded_location is not None
    assert bounded_location[0].field_meta == {
        "type": "file_range",
        "offset": 10,
        "limit": 50,
    }
    assert result_location is not None
    assert result_location[0].field_meta == {
        "type": "file_range",
        "offset": 10,
        "limit": 3,
    }


def test_web_effects_project_urls_and_query_metadata() -> None:
    search = PublicEffectEntry(
        **_entry_fields("web-search", PublicEntryGenerationStatus.COMPLETED),
        title="Search web",
        detail=WebSearchEffectDetail(
            tool_name="extension_web_search",
            input=WebSearchEffectInput(query="python async"),
            display=_display("Searching the web"),
        ),
        state=CompletedEffectState(
            output=WebSearchEffectOutput(
                query="python async",
                answer="Use asyncio",
                sources=[
                    WebSearchEffectSource(
                        title="Python docs", url="https://docs.python.org"
                    )
                ],
            ).model_dump(mode="json", by_alias=True),
            display=EffectResultDisplay(success=True, message="Found 1 source"),
        ),
    )
    fetch_call = PublicEffectEntry(
        **_entry_fields("web-fetch", PublicEntryGenerationStatus.IN_PROGRESS),
        title="Fetch",
        detail=WebFetchEffectDetail(
            tool_name="extension_web_fetch",
            input=WebFetchEffectInput(url="example.com/page"),
            display=_display("Fetching"),
        ),
        state=RunningEffectState(),
    )
    fetch_result = fetch_call.model_copy(
        update={
            "generation_status": PublicEntryGenerationStatus.COMPLETED,
            "state": CompletedEffectState(
                output=WebFetchEffectOutput(
                    url="https://example.com/page",
                    content="hello world",
                    content_type="text/html",
                    was_truncated=True,
                ).model_dump(mode="json", by_alias=True),
                display=EffectResultDisplay(success=True, message="Fetched page"),
            ),
        }
    )

    search_update = replay_history_entry(search)[0]
    fetch_call_update = replay_history_entry(fetch_call)[0]
    fetch_result_update = replay_history_entry(fetch_result)[0]

    assert search_update.field_meta == {
        "tool_name": "extension_web_search",
        "effect_kind": "web_search",
        "query": "python async",
    }
    assert search_update.locations is not None
    assert search_update.locations[0].path == "https://docs.python.org"
    assert search_update.locations[0].field_meta == {
        "type": "url",
        "title": "Python docs",
    }
    assert fetch_call_update.locations is not None
    assert fetch_call_update.locations[0].path == "https://example.com/page"
    assert fetch_result_update.locations is not None
    assert fetch_result_update.locations[0].field_meta == {
        "type": "url",
        "char_count": 11,
        "truncated": True,
    }


def test_skill_effect_projects_name_and_directory_without_name_dispatch() -> None:
    running = PublicEffectEntry(
        **_entry_fields("skill-effect", PublicEntryGenerationStatus.IN_PROGRESS),
        title="Skill",
        detail=SkillEffectDetail(
            tool_name="extension_skill_loader",
            input=SkillEffectInput(name="debug"),
            display=_display("Loading skill"),
        ),
        state=RunningEffectState(),
    )
    completed = running.model_copy(
        update={
            "generation_status": PublicEntryGenerationStatus.COMPLETED,
            "state": CompletedEffectState(
                output=SkillEffectOutput(
                    name="debug", content="instructions", skill_dir="skills/debug"
                ).model_dump(mode="json", by_alias=True),
                display=EffectResultDisplay(success=True, message="Loaded skill"),
            ),
        }
    )

    call = replay_history_entry(running)[0]
    result = replay_history_entry(completed)[0]

    assert call.kind == "read"
    assert call.field_meta == {
        "tool_name": "extension_skill_loader",
        "effect_kind": "skill",
        "skill_name": "debug",
    }
    assert result.locations is not None
    assert result.locations[0].path == str(Path("skills/debug").resolve())


def test_interrupted_subagent_is_failed_and_keeps_task_metadata() -> None:
    entry = PublicEffectEntry(
        **_entry_fields("subagent-effect", PublicEntryGenerationStatus.COMPLETED),
        title="Subagent",
        detail=SubagentEffectDetail(
            tool_name="extension_delegate",
            input=SubagentEffectInput(task="Explore", agent="explore"),
            child_session_id="child-1",
            display=_display("Running subagent"),
        ),
        state=CompletedEffectState(
            output=SubagentEffectOutput(
                response="Interrupted", turns_used=2, completed=False
            ).model_dump(mode="json", by_alias=True),
            display=EffectResultDisplay(
                success=False, message="Agent interrupted after 2 turns"
            ),
        ),
    )

    update = replay_history_entry(entry)[0]

    assert update.status == "failed"
    assert update.field_meta == {
        "tool_name": "extension_delegate",
        "effect_kind": "subagent",
        "agent": "explore",
        "task": "Explore",
        "child_session_id": "child-1",
        "turn_count": 2,
        "response": "Interrupted",
    }


def test_checkpoints_and_session_titles_use_public_identity_and_metadata() -> None:
    checkpoint = PublicCheckpointEntry(
        **_entry_fields("compact-1", PublicEntryGenerationStatus.IN_PROGRESS),
        kind="compaction",
        message="Compacting context",
        details={"currentContextTokens": 10},
    )
    completed = checkpoint.model_copy(
        update={
            "generation_status": PublicEntryGenerationStatus.COMPLETED,
            "message": "Context compacted",
            "details": {"summaryLength": 100},
        }
    )
    title_notice = PublicNoticeEntry(
        **_entry_fields("notice-1", PublicEntryGenerationStatus.COMPLETED),
        level="info",
        message="Session title updated",
        detail=SessionTitleUpdatedNoticeDetail(title="New title"),
    )

    start = replay_history_entry(checkpoint)[0]
    progress = session_updates_for_event(
        HistoryEntryUpdated(
            previous=checkpoint,
            entry=completed,
            patch=[
                JsonPatchOperation(
                    op="replace", path="/generationStatus", value="completed"
                )
            ],
        )
    )[0]
    replayed_title = replay_history_entry(title_notice)[0]
    live_notice_updates = session_updates_for_event(HistoryEntryAdded(title_notice))
    live_title = session_updates_for_event(
        SessionUpdated(
            previous=_session("Old title", 1_000),
            session=_session("New title", 2_000),
            patch=[JsonPatchOperation(op="replace", path="/title", value="New title")],
        )
    )[0]

    assert isinstance(start, ToolCallStart)
    assert start.tool_call_id == "compact-1"
    assert start.status == "in_progress"
    assert isinstance(progress, ToolCallProgress)
    assert progress.tool_call_id == "compact-1"
    assert progress.status == "completed"
    assert progress.raw_output == {"summaryLength": 100}
    assert isinstance(replayed_title, SessionInfoUpdate)
    assert replayed_title.title == "New title"
    assert live_notice_updates == []
    assert isinstance(live_title, SessionInfoUpdate)
    assert live_title.title == "New title"
