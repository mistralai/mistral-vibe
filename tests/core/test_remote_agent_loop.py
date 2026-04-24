from __future__ import annotations

from typing import Any

from tests.conftest import build_test_vibe_config
from vibe.core.nuage.events import (
    CustomTaskCanceled,
    CustomTaskCanceledAttributes,
    CustomTaskCompleted,
    CustomTaskCompletedAttributes,
    CustomTaskInProgress,
    CustomTaskInProgressAttributes,
    CustomTaskStarted,
    CustomTaskStartedAttributes,
    JSONPatchAdd,
    JSONPatchAppend,
    JSONPatchPayload,
    JSONPatchReplace,
    JSONPayload,
)
from vibe.core.nuage.remote_events_source import RemoteEventsSource
from vibe.core.types import (
    AssistantEvent,
    ReasoningEvent,
    Role,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
    WaitingForInputEvent,
)

_EXEC_ID = "session-123"


def _make_loop(enabled_tools: list[str] | None = None) -> RemoteEventsSource:
    config = build_test_vibe_config(enabled_tools=enabled_tools or [])
    return RemoteEventsSource(session_id=_EXEC_ID, config=config)


def _started(
    task_id: str, task_type: str, payload: dict[str, Any]
) -> CustomTaskStarted:
    return CustomTaskStarted(
        event_id=f"evt-{task_id}-start",
        workflow_exec_id=_EXEC_ID,
        attributes=CustomTaskStartedAttributes(
            custom_task_id=task_id,
            custom_task_type=task_type,
            payload=JSONPayload(value=payload),
        ),
    )


def _completed(
    task_id: str, task_type: str, payload: dict[str, Any]
) -> CustomTaskCompleted:
    return CustomTaskCompleted(
        event_id=f"evt-{task_id}-done",
        workflow_exec_id=_EXEC_ID,
        attributes=CustomTaskCompletedAttributes(
            custom_task_id=task_id,
            custom_task_type=task_type,
            payload=JSONPayload(value=payload),
        ),
    )


def _in_progress(
    task_id: str, task_type: str, patches: list[Any]
) -> CustomTaskInProgress:
    return CustomTaskInProgress(
        event_id=f"evt-{task_id}-progress",
        workflow_exec_id=_EXEC_ID,
        attributes=CustomTaskInProgressAttributes(
            custom_task_id=task_id,
            custom_task_type=task_type,
            payload=JSONPatchPayload(value=patches),
        ),
    )


def _canceled(task_id: str, task_type: str, reason: str = "") -> CustomTaskCanceled:
    return CustomTaskCanceled(
        event_id=f"evt-{task_id}-cancel",
        workflow_exec_id=_EXEC_ID,
        attributes=CustomTaskCanceledAttributes(
            custom_task_id=task_id, custom_task_type=task_type, reason=reason
        ),
    )


def test_consume_wait_for_input_event_emits_waiting_event() -> None:
    loop = _make_loop()
    event = _started(
        "wait-task-1",
        "wait_for_input",
        {
            "task_id": "wait-task-1",
            "input_schema": {"title": "ChatInput"},
            "label": "What next?",
        },
    )

    emitted_events = loop._consume_workflow_event(event)

    assert len(emitted_events) == 2
    assistant_event = emitted_events[0]
    waiting_event = emitted_events[1]
    assert isinstance(assistant_event, AssistantEvent)
    assert assistant_event.content == "What next?"
    assert isinstance(waiting_event, WaitingForInputEvent)
    assert waiting_event.task_id == "wait-task-1"
    assert waiting_event.label == "What next?"
    assert waiting_event.predefined_answers is None


def test_consume_agent_input_keeps_repeated_text_across_distinct_turns() -> None:
    loop = _make_loop()
    first_event = _completed(
        "input-1", "AgentInputState", {"input": {"message": [{"text": "continue"}]}}
    )
    second_event = _completed(
        "input-2", "AgentInputState", {"input": {"message": [{"text": "continue"}]}}
    )

    assert loop._consume_workflow_event(first_event) == []
    assert loop._consume_workflow_event(second_event) == []

    assert [msg.content for msg in loop.messages if msg.role == Role.user] == [
        "continue",
        "continue",
    ]


def test_wait_for_input_emits_predefined_answers_and_user_message() -> None:
    loop = _make_loop()
    started = _started(
        "wait-task-1",
        "wait_for_input",
        {
            "input_schema": {
                "title": "ChatInput",
                "properties": {
                    "message": {
                        "examples": [
                            [{"type": "text", "text": "Python"}],
                            [{"type": "text", "text": "JavaScript"}],
                            [{"type": "text", "text": "Other"}],
                        ]
                    }
                },
            },
            "label": "Which language?",
        },
    )
    completed = _completed(
        "wait-task-1",
        "wait_for_input",
        {"input": {"message": [{"type": "text", "text": "Python"}]}},
    )

    started_events = loop._consume_workflow_event(started)
    completed_events = loop._consume_workflow_event(completed)

    assistant_event = next(
        event for event in started_events if isinstance(event, AssistantEvent)
    )
    waiting_event = next(
        event for event in started_events if isinstance(event, WaitingForInputEvent)
    )
    assert assistant_event.content == "Which language?"
    assert waiting_event.predefined_answers == ["Python", "JavaScript"]
    user_event = next(
        event for event in completed_events if isinstance(event, UserMessageEvent)
    )
    assert user_event.content == "Python"


def test_tool_events_update_stats_and_messages() -> None:
    loop = _make_loop(enabled_tools=["todo"])
    started = _started(
        "tool-task-1",
        "AgentToolCallState",
        {"name": "todo", "tool_call_id": "call-1", "kwargs": {"action": "read"}},
    )
    completed = _completed(
        "tool-task-1",
        "AgentToolCallState",
        {
            "name": "todo",
            "tool_call_id": "call-1",
            "kwargs": {"action": "read"},
            "output": {"total_count": 0},
        },
    )

    started_events = loop._consume_workflow_event(started)
    completed_events = loop._consume_workflow_event(completed)

    assert any(isinstance(event, ToolCallEvent) for event in started_events)
    result_event = next(
        event for event in completed_events if isinstance(event, ToolResultEvent)
    )
    assert result_event.error is None
    assert result_event.cancelled is False
    assert result_event.tool_call_id == "call-1"
    assert loop.stats.tool_calls_agreed == 1
    assert loop.stats.tool_calls_succeeded == 1
    assert loop.stats.tool_calls_failed == 0
    tool_messages = [msg for msg in loop.messages if msg.role == Role.tool]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call-1"


def test_ask_user_question_tool_emits_assistant_question() -> None:
    loop = _make_loop(enabled_tools=["ask_user_question"])
    started = _started(
        "tool-task-question",
        "AgentToolCallState",
        {
            "name": "ask_user_question",
            "tool_call_id": "call-question",
            "kwargs": {
                "questions": [
                    {
                        "question": "Which file type should I create?",
                        "options": [{"label": "Python"}, {"label": "JavaScript"}],
                    }
                ]
            },
        },
    )

    events = loop._consume_workflow_event(started)

    assistant_event = next(
        event for event in events if isinstance(event, AssistantEvent)
    )
    tool_call_event = next(
        event for event in events if isinstance(event, ToolCallEvent)
    )
    assert assistant_event.content == "Which file type should I create?"
    assert tool_call_event.tool_call_id == "call-question"


def test_ask_user_question_wait_for_input_completion_emits_tool_result() -> None:
    loop = _make_loop(enabled_tools=["ask_user_question"])
    ask_started = _started(
        "tool-task-question",
        "AgentToolCallState",
        {
            "name": "ask_user_question",
            "tool_call_id": "call-question",
            "kwargs": {
                "questions": [{"question": "Which type of file?", "options": []}]
            },
        },
    )
    wait_started = _started(
        "wait-task-1",
        "wait_for_input",
        {"input_schema": {"title": "ChatInput"}, "label": "Which type of file?"},
    )
    wait_completed = _completed(
        "wait-task-1",
        "wait_for_input",
        {
            "input_schema": {"title": "ChatInput"},
            "label": "Which type of file?",
            "input": {"message": [{"type": "text", "text": "Python"}]},
        },
    )

    loop._consume_workflow_event(ask_started)
    loop._consume_workflow_event(wait_started)
    completed_events = loop._consume_workflow_event(wait_completed)

    tool_result = next(
        (e for e in completed_events if isinstance(e, ToolResultEvent)), None
    )
    assert tool_result is not None
    assert tool_result.tool_call_id == "call-question"
    user_message = next(e for e in completed_events if isinstance(e, UserMessageEvent))
    assert user_message.content == "Python"


def test_working_events_without_tool_call_id_render_remote_progress_row() -> None:
    loop = _make_loop()
    started = _started(
        "working-1",
        "working",
        {"title": "Creating sandbox", "content": "initializing", "toolUIState": None},
    )
    completed = _completed(
        "working-1",
        "working",
        {
            "title": "Creating sandbox",
            "content": "sandbox created",
            "toolUIState": None,
        },
    )

    started_events = loop._consume_workflow_event(started)
    completed_events = loop._consume_workflow_event(completed)

    assert started_events == []
    assert any(isinstance(event, ToolCallEvent) for event in completed_events)
    assert any(isinstance(event, ToolStreamEvent) for event in completed_events)
    result_event = next(
        event for event in completed_events if isinstance(event, ToolResultEvent)
    )
    assert result_event.tool_name == "Creating sandbox"
    assert result_event.tool_call_id == "working-1"


def test_working_events_with_tool_call_id_wait_for_real_tool_call() -> None:
    loop = _make_loop()
    working_started = _started(
        "working-tool-1",
        "working",
        {
            "title": "Executing write_file",
            "content": "writing file",
            "toolUIState": {"toolCallId": "call-write"},
        },
    )
    tool_started = _started(
        "tool-task-1",
        "AgentToolCallState",
        {
            "name": "write_file",
            "tool_call_id": "call-write",
            "kwargs": {
                "path": "hello_world.js",
                "content": "console.log('Hello, World!');",
            },
        },
    )

    working_events = loop._consume_workflow_event(working_started)
    tool_events = loop._consume_workflow_event(tool_started)

    assert any(isinstance(event, ToolCallEvent) for event in working_events)
    assert any(isinstance(event, ToolStreamEvent) for event in working_events)
    assert not any(isinstance(event, ToolCallEvent) for event in tool_events)
    assert not any(isinstance(event, ToolResultEvent) for event in tool_events)


def test_working_task_promoted_to_real_tool_call_does_not_create_duplicate_row() -> (
    None
):
    loop = _make_loop(enabled_tools=["write_file"])
    working_started = _started(
        "working-tool-1",
        "working",
        {
            "title": "Writing file",
            "content": '# hello.py\n\nprint("Hello, World!")',
            "toolUIState": None,
        },
    )
    working_promoted = _completed(
        "working-tool-1",
        "working",
        {
            "title": "Executing write_file",
            "content": "",
            "toolUIState": {
                "type": "file",
                "toolCallId": "call-write",
                "operations": [
                    {
                        "type": "create",
                        "uri": "/workspace/hello.py",
                        "content": 'print("Hello, World!")',
                    }
                ],
            },
        },
    )
    agent_tool_completed = _completed(
        "tool-task-1",
        "AgentToolCallState",
        {
            "name": "write_file",
            "tool_call_id": "call-write",
            "kwargs": {"path": "hello.py", "content": 'print("Hello, World!")'},
            "output": {
                "path": "/workspace/hello.py",
                "bytes_written": 22,
                "file_existed": False,
                "content": 'print("Hello, World!")',
            },
        },
    )

    assert loop._consume_workflow_event(working_started) == []

    promoted_events = loop._consume_workflow_event(working_promoted)
    assert len([e for e in promoted_events if isinstance(e, ToolCallEvent)]) == 1
    assert not any(isinstance(e, ToolStreamEvent) for e in promoted_events)
    assert any(isinstance(e, ToolResultEvent) for e in promoted_events)

    completed_events = loop._consume_workflow_event(agent_tool_completed)
    assert not any(isinstance(e, ToolCallEvent) for e in completed_events)
    assert not any(isinstance(e, ToolResultEvent) for e in completed_events)


def test_idle_boundary_waits_for_open_tool_results() -> None:
    loop = _make_loop(enabled_tools=["write_file"])
    working_started = _started(
        "working-tool-1",
        "working",
        {
            "title": "Executing write_file",
            "content": "writing file",
            "toolUIState": {"toolCallId": "call-write"},
        },
    )
    idle_candidate = _completed("input-task-1", "AgentInputState", {"input": None})
    tool_completed = _completed(
        "tool-task-1",
        "AgentToolCallState",
        {
            "name": "write_file",
            "tool_call_id": "call-write",
            "kwargs": {
                "path": "hello_world.js",
                "content": "console.log('Hello, World!');",
            },
            "output": {
                "path": "/workspace/hello_world.js",
                "bytes_written": 29,
                "file_existed": False,
                "content": "console.log('Hello, World!');",
            },
        },
    )
    idle_after_tool = _completed("input-task-2", "AgentInputState", {"input": None})

    working_events = loop._consume_workflow_event(working_started)
    assert any(isinstance(event, ToolCallEvent) for event in working_events)

    loop._consume_workflow_event(idle_candidate)
    assert loop._is_idle_boundary(idle_candidate) is False

    tool_events = loop._consume_workflow_event(tool_completed)
    assert not any(isinstance(event, ToolCallEvent) for event in tool_events)
    assert any(isinstance(event, ToolResultEvent) for event in tool_events)

    loop._consume_workflow_event(idle_after_tool)
    assert loop._is_idle_boundary(idle_after_tool) is True


def test_send_user_message_tool_is_not_rendered() -> None:
    loop = _make_loop()
    started = _started(
        "tool-task-send-user-message",
        "AgentToolCallState",
        {
            "name": "send_user_message",
            "tool_call_id": "call-send",
            "kwargs": {"message": "hello"},
        },
    )
    completed = _completed(
        "tool-task-send-user-message",
        "AgentToolCallState",
        {
            "name": "send_user_message",
            "tool_call_id": "call-send",
            "kwargs": {"message": "hello"},
            "output": {"success": True, "error": None},
        },
    )

    assert loop._consume_workflow_event(started) == []
    assert loop._consume_workflow_event(completed) == []


def test_send_user_message_working_events_are_not_rendered() -> None:
    loop = _make_loop()
    started = _started(
        "working-send-user-message",
        "working",
        {
            "title": "Executing send_user_message",
            "content": "Hello!",
            "toolUIState": {"toolCallId": "call-send-working"},
        },
    )
    completed = _completed(
        "working-send-user-message",
        "working",
        {
            "title": "Executing send_user_message",
            "content": "Hello!",
            "toolUIState": {"toolCallId": "call-send-working"},
        },
    )

    assert loop._consume_workflow_event(started) == []
    assert loop._consume_workflow_event(completed) == []


def test_remote_bash_uses_known_tool_display_even_when_disabled_locally() -> None:
    loop = _make_loop(enabled_tools=["write_file"])
    started = _started(
        "tool-task-bash",
        "AgentToolCallState",
        {
            "name": "bash",
            "tool_call_id": "call-bash",
            "kwargs": {"command": "cat hello.py | wc -c"},
        },
    )
    completed = _completed(
        "tool-task-bash",
        "AgentToolCallState",
        {
            "name": "bash",
            "tool_call_id": "call-bash",
            "kwargs": {"command": "cat hello.py | wc -c"},
            "output": {
                "command": "cat hello.py | wc -c",
                "stdout": "22\n",
                "stderr": "",
                "returncode": 0,
            },
        },
    )

    started_events = loop._consume_workflow_event(started)
    completed_events = loop._consume_workflow_event(completed)

    tool_call_event = next(
        event for event in started_events if isinstance(event, ToolCallEvent)
    )
    result_event = next(
        event for event in completed_events if isinstance(event, ToolResultEvent)
    )

    assert tool_call_event.tool_name == "bash"
    assert tool_call_event.tool_class.get_name() == "bash"
    assert tool_call_event.args is not None
    assert tool_call_event.args.command == "cat hello.py | wc -c"  # type: ignore[attr-defined]
    assert result_event.result is not None
    assert result_event.result.command == "cat hello.py | wc -c"  # type: ignore[attr-defined]
    assert result_event.result.stdout == "22\n"  # type: ignore[attr-defined]


def test_canceled_tool_marks_cancelled_and_failed_stats() -> None:
    loop = _make_loop(enabled_tools=["todo"])
    loop._task_state["tool-task-2"] = {
        "name": "todo",
        "tool_call_id": "call-2",
        "kwargs": {"action": "read"},
    }
    canceled = _canceled(
        "tool-task-2", "AgentToolCallState", reason="user interrupted tool"
    )

    events = loop._consume_workflow_event(canceled)

    result_event = next(event for event in events if isinstance(event, ToolResultEvent))
    assert result_event.cancelled is True
    assert result_event.error == "Canceled: user interrupted tool"
    assert loop.stats.tool_calls_failed == 1
    assert loop.stats.tool_calls_succeeded == 0


def test_working_thinking_type_emits_assistant_events() -> None:
    loop = _make_loop()
    started = _started(
        "thinking-1",
        "working",
        {"type": "thinking", "title": "Thinking", "content": "", "toolUIState": None},
    )
    in_progress = _in_progress(
        "thinking-1", "working", [JSONPatchAppend(path="/content", value="Hello!")]
    )
    completed = _completed(
        "thinking-1",
        "working",
        {
            "type": "thinking",
            "title": "Thinking",
            "content": "Hello!",
            "toolUIState": None,
        },
    )

    started_events = loop._consume_workflow_event(started)
    progress_events = loop._consume_workflow_event(in_progress)
    completed_events = loop._consume_workflow_event(completed)

    assert started_events == []
    assert len(progress_events) == 1
    assert isinstance(progress_events[0], ReasoningEvent)
    assert progress_events[0].content == "Hello!"
    assert completed_events == []


def test_working_bash_progress_without_tool_call_id_streams_command_output() -> None:
    loop = _make_loop(enabled_tools=["write_file"])
    started = _started(
        "working-bash-1",
        "working",
        {"type": "tool", "title": "Planning", "content": "", "toolUIState": None},
    )
    in_progress = _in_progress(
        "working-bash-1",
        "working",
        [
            JSONPatchAdd(
                path="/toolUIState",
                value={
                    "type": "command",
                    "command": "ls -la /workspace",
                    "result": {
                        "status": "success",
                        "output": "total 4\ndrwxrwxrwx 2 root root 4096 Mar 20 10:18 .\ndrwxr-xr-x 1 root root   80 Mar 20 10:18 ..\n",
                    },
                },
            ),
            JSONPatchReplace(path="/title", value="Executing bash"),
            JSONPatchReplace(path="/content", value=""),
        ],
    )

    started_events = loop._consume_workflow_event(started)
    progress_events = loop._consume_workflow_event(in_progress)

    assert started_events == []
    tool_call_event = next(
        event for event in progress_events if isinstance(event, ToolCallEvent)
    )
    tool_stream_event = next(
        event for event in progress_events if isinstance(event, ToolStreamEvent)
    )

    assert tool_call_event.tool_name == "bash"
    assert tool_call_event.tool_class.get_name() == "bash"
    assert tool_call_event.tool_call_id == "working-bash-1"
    assert tool_stream_event.tool_name == "bash"
    assert tool_stream_event.tool_call_id == "working-bash-1"
    assert "command: ls -la /workspace" in tool_stream_event.message
    assert "total 4" in tool_stream_event.message
    assert "drwxrwxrwx 2 root root 4096" in tool_stream_event.message


def test_working_completed_with_tool_call_id_emits_tool_result() -> None:
    loop = _make_loop(enabled_tools=["write_file"])
    working_started = _started(
        "working-tool-1",
        "working",
        {
            "title": "Executing write_file",
            "content": "",
            "toolUIState": {"toolCallId": "call-write-solo"},
        },
    )
    working_completed = _completed(
        "working-tool-1",
        "working",
        {
            "title": "Executing write_file",
            "content": "",
            "toolUIState": {
                "type": "file",
                "toolCallId": "call-write-solo",
                "operations": [
                    {
                        "type": "create",
                        "uri": "/workspace/hello.py",
                        "content": 'print("Hello, World!")',
                    }
                ],
            },
        },
    )

    started_events = loop._consume_workflow_event(working_started)
    assert any(isinstance(e, ToolCallEvent) for e in started_events)

    completed_events = loop._consume_workflow_event(working_completed)
    result_events = [e for e in completed_events if isinstance(e, ToolResultEvent)]
    assert len(result_events) == 1
    assert result_events[0].error is None
    assert result_events[0].tool_call_id == "call-write-solo"


def test_working_completed_with_tool_call_id_emits_error_result() -> None:
    loop = _make_loop(enabled_tools=["write_file"])
    working_started = _started(
        "working-tool-2",
        "working",
        {
            "title": "Executing write_file",
            "content": "",
            "toolUIState": {"toolCallId": "call-write-err"},
        },
    )
    working_completed = _completed(
        "working-tool-2",
        "working",
        {
            "title": "Executing write_file",
            "content": "Error: File exists. Set overwrite=True.",
            "toolUIState": {
                "type": "file",
                "toolCallId": "call-write-err",
                "operations": [],
            },
        },
    )

    loop._consume_workflow_event(working_started)
    completed_events = loop._consume_workflow_event(working_completed)

    result_events = [e for e in completed_events if isinstance(e, ToolResultEvent)]
    assert len(result_events) == 1
    assert result_events[0].error is not None
    assert result_events[0].tool_call_id == "call-write-err"


def test_json_patch_with_array_index_preserves_list_structure() -> None:
    loop = _make_loop()
    started = _started(
        "msg-1",
        "assistant_message",
        {"contentChunks": [{"type": "text", "text": "Hello"}]},
    )
    in_progress = _in_progress(
        "msg-1",
        "assistant_message",
        [JSONPatchReplace(path="/contentChunks/0/text", value="Hello world")],
    )

    loop._consume_workflow_event(started)
    progress_events = loop._consume_workflow_event(in_progress)

    assert len(progress_events) == 1
    assert isinstance(progress_events[0], AssistantEvent)
    assert progress_events[0].content == " world"


def test_steer_input_events_are_suppressed() -> None:
    loop = _make_loop()
    steer_started = _started(
        "steer-1",
        "wait_for_input",
        {"input_schema": {"title": "ChatInput"}, "label": "Send a message to steer..."},
    )
    steer_completed = _completed(
        "steer-1",
        "wait_for_input",
        {
            "input_schema": {"title": "ChatInput"},
            "label": "Send a message to steer...",
            "input": None,
        },
    )

    assert loop._consume_workflow_event(steer_started) == []
    assert loop._consume_workflow_event(steer_completed) == []
    assert loop._translator.pending_input_request is None
