from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_tool import FakeTool
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfig
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.builtins.todo import TodoItem
from vibe.core.types import (
    ApprovalCallback,
    ApprovalResponse,
    AssistantEvent,
    BaseEvent,
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)


async def act_and_collect_events(agent_loop: AgentLoop, prompt: str) -> list[BaseEvent]:
    return [ev async for ev in agent_loop.act(prompt)]


def make_config(todo_permission: ToolPermission = ToolPermission.ALWAYS) -> VibeConfig:
    return build_test_vibe_config(
        enabled_tools=["todo"],
        tools={"todo": {"permission": todo_permission.value}},
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )


def make_todo_tool_call(
    call_id: str, index: int = 0, arguments: str | None = None
) -> ToolCall:
    args = arguments if arguments is not None else '{"action": "read"}'
    return ToolCall(
        id=call_id, index=index, function=FunctionCall(name="todo", arguments=args)
    )


def make_agent_loop(
    *,
    auto_approve: bool = True,
    todo_permission: ToolPermission = ToolPermission.ALWAYS,
    backend: FakeBackend,
    approval_callback: ApprovalCallback | None = None,
) -> AgentLoop:
    agent_name = (
        BuiltinAgentName.AUTO_APPROVE if auto_approve else BuiltinAgentName.DEFAULT
    )
    agent_loop = build_test_agent_loop(
        config=make_config(todo_permission=todo_permission),
        agent_name=agent_name,
        backend=backend,
    )
    if approval_callback:
        agent_loop.set_approval_callback(approval_callback)
    return agent_loop


@pytest.mark.asyncio
async def test_single_tool_call_executes_under_auto_approve(
    telemetry_events: list[dict],
) -> None:
    mocked_tool_call_id = "call_1"
    tool_call = make_todo_tool_call(mocked_tool_call_id)
    backend = FakeBackend([
        [mock_llm_chunk(content="Let me check your todos.", tool_calls=[tool_call])],
        [mock_llm_chunk(content="I retrieved 0 todos.")],
    ])
    agent_loop = make_agent_loop(auto_approve=True, backend=backend)

    events = await act_and_collect_events(agent_loop, "What's my todo list?")

    assert [type(e) for e in events] == [
        UserMessageEvent,
        AssistantEvent,
        ToolCallEvent,
        ToolResultEvent,
        AssistantEvent,
    ]
    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[1], AssistantEvent)
    assert events[1].content == "Let me check your todos."
    assert isinstance(events[2], ToolCallEvent)
    assert events[2].tool_name == "todo"
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].error is None
    assert events[3].skipped is False
    assert events[3].result is not None
    assert isinstance(events[4], AssistantEvent)
    assert events[4].content == "I retrieved 0 todos."
    # check conversation history
    tool_msgs = [m for m in agent_loop.messages if m.role == Role.tool]
    assert len(tool_msgs) == 1
    assert tool_msgs[-1].tool_call_id == mocked_tool_call_id
    assert "total_count" in (tool_msgs[-1].content or "")

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 1
    assert tool_finished[0]["properties"]["tool_name"] == "todo"
    assert tool_finished[0]["properties"]["status"] == "success"
    assert tool_finished[0]["properties"]["approval_type"] == "always"


@pytest.mark.asyncio
async def test_tool_call_requires_approval_if_not_auto_approved(
    telemetry_events: list[dict],
) -> None:
    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        backend=FakeBackend([
            [
                mock_llm_chunk(
                    content="Let me check your todos.",
                    tool_calls=[make_todo_tool_call("call_2")],
                )
            ],
            [mock_llm_chunk(content="I cannot execute the tool without approval.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "What's my todo list?")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[1], AssistantEvent)
    assert isinstance(events[2], ToolCallEvent)
    assert events[2].tool_name == "todo"
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].skipped is True
    assert events[3].error is None
    assert events[3].result is None
    assert events[3].skip_reason is not None
    assert "not permitted" in events[3].skip_reason.lower()
    assert isinstance(events[4], AssistantEvent)
    assert events[4].content == "I cannot execute the tool without approval."
    assert agent_loop.stats.tool_calls_rejected == 1
    assert agent_loop.stats.tool_calls_agreed == 0
    assert agent_loop.stats.tool_calls_succeeded == 0

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 1
    assert tool_finished[0]["properties"]["approval_type"] == "ask"


@pytest.mark.asyncio
async def test_tool_call_approved_by_callback(telemetry_events: list[dict]) -> None:
    async def approval_callback(
        _tool_name: str, _args: BaseModel, _tool_call_id: str, _rp: list | None = None
    ) -> tuple[ApprovalResponse, str | None]:
        return (ApprovalResponse.YES, None)

    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        approval_callback=approval_callback,
        backend=FakeBackend([
            [
                mock_llm_chunk(
                    content="Let me check your todos.",
                    tool_calls=[make_todo_tool_call("call_3")],
                )
            ],
            [mock_llm_chunk(content="I retrieved 0 todos.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "What's my todo list?")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].skipped is False
    assert events[3].error is None
    assert events[3].result is not None
    assert agent_loop.stats.tool_calls_agreed == 1
    assert agent_loop.stats.tool_calls_rejected == 0
    assert agent_loop.stats.tool_calls_succeeded == 1

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 1
    assert tool_finished[0]["properties"]["approval_type"] == "ask"


@pytest.mark.asyncio
async def test_tool_call_rejected_when_auto_approve_disabled_and_rejected_by_callback(
    telemetry_events: list[dict],
) -> None:
    custom_feedback = "User declined tool execution"

    async def approval_callback(
        _tool_name: str, _args: BaseModel, _tool_call_id: str, _rp: list | None = None
    ) -> tuple[ApprovalResponse, str | None]:
        return (ApprovalResponse.NO, custom_feedback)

    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        approval_callback=approval_callback,
        backend=FakeBackend([
            [
                mock_llm_chunk(
                    content="Let me check your todos.",
                    tool_calls=[make_todo_tool_call("call_4")],
                )
            ],
            [mock_llm_chunk(content="Understood, I won't check the todos.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "What's my todo list?")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].skipped is True
    assert events[3].error is None
    assert events[3].result is None
    assert events[3].skip_reason == custom_feedback
    assert events[3].cancelled is False
    assert agent_loop.stats.tool_calls_rejected == 1
    assert agent_loop.stats.tool_calls_agreed == 0
    assert agent_loop.stats.tool_calls_succeeded == 0

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 1
    assert tool_finished[0]["properties"]["approval_type"] == "ask"


@pytest.mark.asyncio
async def test_tool_call_skipped_when_permission_is_never(
    telemetry_events: list[dict],
) -> None:
    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.NEVER,
        backend=FakeBackend([
            [
                mock_llm_chunk(
                    content="Let me check your todos.",
                    tool_calls=[make_todo_tool_call("call_never")],
                )
            ],
            [mock_llm_chunk(content="Tool is disabled.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "What's my todo list?")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].skipped is True
    assert events[3].error is None
    assert events[3].result is None
    assert events[3].skip_reason is not None
    assert "permanently disabled" in events[3].skip_reason.lower()
    tool_msgs = [
        m for m in agent_loop.messages if m.role == Role.tool and m.name == "todo"
    ]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].name == "todo"
    assert events[3].skip_reason in (tool_msgs[-1].content or "")
    assert agent_loop.stats.tool_calls_rejected == 1
    assert agent_loop.stats.tool_calls_agreed == 0
    assert agent_loop.stats.tool_calls_succeeded == 0

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 1
    assert tool_finished[0]["properties"]["approval_type"] == "never"


@pytest.mark.asyncio
async def test_approval_always_sets_tool_permission_for_subsequent_calls() -> None:
    callback_invocations = []
    agent_ref: AgentLoop | None = None

    async def approval_callback(
        tool_name: str, _args: BaseModel, _tool_call_id: str, _rp: list | None = None
    ) -> tuple[ApprovalResponse, str | None]:
        callback_invocations.append(tool_name)
        # Set permission to ALWAYS for this tool (simulating the new behavior)
        assert agent_ref is not None
        if tool_name not in agent_ref.config.tools:
            agent_ref.config.tools[tool_name] = {}
        agent_ref.config.tools[tool_name]["permission"] = "always"
        return (ApprovalResponse.YES, None)

    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        approval_callback=approval_callback,
        backend=FakeBackend([
            [
                mock_llm_chunk(
                    content="First check.",
                    tool_calls=[make_todo_tool_call("call_first")],
                )
            ],
            [mock_llm_chunk(content="First done.")],
            [
                mock_llm_chunk(
                    content="Second check.",
                    tool_calls=[make_todo_tool_call("call_second")],
                )
            ],
            [mock_llm_chunk(content="Second done.")],
        ]),
    )
    agent_ref = agent_loop

    events1 = await act_and_collect_events(agent_loop, "First request")
    events2 = await act_and_collect_events(agent_loop, "Second request")

    tool_config_todo = agent_loop.tool_manager.get_tool_config("todo")
    assert tool_config_todo.permission is ToolPermission.ALWAYS
    tool_config_help = agent_loop.tool_manager.get_tool_config("bash")
    assert tool_config_help.permission is not ToolPermission.ALWAYS
    assert agent_loop.auto_approve is False
    assert len(callback_invocations) == 1
    assert callback_invocations[0] == "todo"
    assert isinstance(events1[0], UserMessageEvent)
    assert isinstance(events1[3], ToolResultEvent)
    assert events1[3].skipped is False
    assert events1[3].result is not None
    assert isinstance(events2[0], UserMessageEvent)
    assert isinstance(events2[3], ToolResultEvent)
    assert events2[3].skipped is False
    assert events2[3].result is not None
    assert agent_loop.stats.tool_calls_rejected == 0
    assert agent_loop.stats.tool_calls_succeeded == 2


@pytest.mark.asyncio
async def test_tool_call_with_invalid_action() -> None:
    tool_call = make_todo_tool_call("call_5", arguments='{"action": "invalid_action"}')
    agent_loop = make_agent_loop(
        auto_approve=True,
        backend=FakeBackend([
            [
                mock_llm_chunk(
                    content="Let me check your todos.", tool_calls=[tool_call]
                )
            ],
            [mock_llm_chunk(content="I encountered an error with the action.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "What's my todo list?")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].error is not None
    assert events[3].result is None
    assert "tool_error" in events[3].error.lower()
    assert agent_loop.stats.tool_calls_failed == 1


@pytest.mark.asyncio
async def test_tool_call_with_duplicate_todo_ids() -> None:
    duplicate_todos = [
        TodoItem(id="duplicate", content="Task 1"),
        TodoItem(id="duplicate", content="Task 2"),
    ]
    tool_call = make_todo_tool_call(
        "call_6",
        arguments=json.dumps({
            "action": "write",
            "todos": [t.model_dump() for t in duplicate_todos],
        }),
    )
    agent_loop = make_agent_loop(
        auto_approve=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Let me write todos.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="I couldn't write todos with duplicate IDs.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Add todos")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].error is not None
    assert events[3].result is None
    assert "unique" in events[3].error.lower()
    assert agent_loop.stats.tool_calls_failed == 1


@pytest.mark.asyncio
async def test_tool_call_with_exceeding_max_todos() -> None:
    many_todos = [TodoItem(id=f"todo_{i}", content=f"Task {i}") for i in range(150)]
    tool_call = make_todo_tool_call(
        "call_7",
        arguments=json.dumps({
            "action": "write",
            "todos": [t.model_dump() for t in many_todos],
        }),
    )
    agent_loop = make_agent_loop(
        auto_approve=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Let me write todos.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="I couldn't write that many todos.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Add todos")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[3], ToolResultEvent)
    assert events[3].error is not None
    assert events[3].result is None
    assert "100" in events[3].error
    assert agent_loop.stats.tool_calls_failed == 1


@pytest.mark.asyncio
async def test_tool_call_can_be_interrupted() -> None:
    """Test that tool calls can be interrupted via asyncio.CancelledError.

    When a tool raises CancelledError, the error is captured as a cancellation event
    and the agent loop stops gracefully after the current tool batch completes.
    """
    tool_call = ToolCall(
        id="call_8", index=0, function=FunctionCall(name="stub_tool", arguments="{}")
    )
    config = build_test_vibe_config(enabled_tools=["stub_tool"])
    agent_loop = build_test_agent_loop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=FakeBackend([
            [mock_llm_chunk(content="Let me use the tool.", tool_calls=[tool_call])],
            [mock_llm_chunk(content="Tool execution completed.")],
        ]),
    )
    # no dependency injection available => monkey patch
    agent_loop.tool_manager._available["stub_tool"] = FakeTool
    stub_tool_instance = agent_loop.tool_manager.get("stub_tool")
    assert isinstance(stub_tool_instance, FakeTool)
    stub_tool_instance._exception_to_raise = asyncio.CancelledError()

    events: list[BaseEvent] = []
    async for ev in agent_loop.act("Execute tool"):
        events.append(ev)

    tool_result_event = next(
        (e for e in events if isinstance(e, ToolResultEvent)), None
    )
    assert tool_result_event is not None
    assert tool_result_event.error is not None
    assert "execution interrupted by user" in tool_result_event.error.lower()
    assert agent_loop.stats.tool_calls_failed == 1

    # Agent loop should stop after cancellation — no second LLM turn
    assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
    assert len(assistant_events) == 1


@pytest.mark.asyncio
async def test_fill_missing_tool_responses_inserts_placeholders() -> None:
    agent_loop = build_test_agent_loop(
        config=make_config(),
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=FakeBackend(mock_llm_chunk(content="ok")),
    )
    tool_calls_messages = [
        make_todo_tool_call("tc1", index=0),
        make_todo_tool_call("tc2", index=1),
    ]
    assistant_msg = LLMMessage(
        role=Role.assistant, content="Calling tools...", tool_calls=tool_calls_messages
    )
    agent_loop.messages.reset([
        agent_loop.messages[0],
        assistant_msg,
        # only one tool responded: the second is missing
        LLMMessage(
            role=Role.tool, tool_call_id="tc1", name="todo", content="Retrieved 0 todos"
        ),
    ])

    await act_and_collect_events(agent_loop, "Proceed")

    tool_msgs = [m for m in agent_loop.messages if m.role == Role.tool]
    assert any(m.tool_call_id == "tc2" for m in tool_msgs)
    # find placeholder message for tc2
    placeholder = next(m for m in tool_msgs if m.tool_call_id == "tc2")
    assert placeholder.name == "todo"
    assert (
        placeholder.content
        == "<user_cancellation>Tool execution interrupted - no response available</user_cancellation>"
    )


@pytest.mark.asyncio
async def test_ensure_assistant_after_tool_appends_understood() -> None:
    agent_loop = build_test_agent_loop(
        config=make_config(),
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=FakeBackend(mock_llm_chunk(content="ok")),
    )
    tool_msg = LLMMessage(
        role=Role.tool, tool_call_id="tc_z", name="todo", content="Done"
    )
    agent_loop.messages.reset([agent_loop.messages[0], tool_msg])

    await act_and_collect_events(agent_loop, "Next")

    # find the seeded tool message and ensure the next message is "Understood."
    idx = next(i for i, m in enumerate(agent_loop.messages) if m.role == Role.tool)
    assert agent_loop.messages[idx + 1].role == Role.assistant
    assert agent_loop.messages[idx + 1].content == "Understood."


@pytest.mark.asyncio
async def test_parallel_tool_calls_produce_correct_events(
    telemetry_events: list[dict],
) -> None:
    """Two tool calls in one LLM response should execute in parallel and produce correct events."""
    tool_call_1 = make_todo_tool_call("call_p1", index=0)
    tool_call_2 = make_todo_tool_call("call_p2", index=1)
    backend = FakeBackend([
        [
            mock_llm_chunk(
                content="Let me check two things.",
                tool_calls=[tool_call_1, tool_call_2],
            )
        ],
        [mock_llm_chunk(content="Both done.")],
    ])
    agent_loop = make_agent_loop(auto_approve=True, backend=backend)

    events = await act_and_collect_events(agent_loop, "Check two things")

    event_types = [type(e) for e in events]
    # UserMessage, Assistant, ToolCall, ToolCall, then two ToolResults (order may vary), then Assistant
    assert event_types[0] is UserMessageEvent
    assert event_types[1] is AssistantEvent
    # Both ToolCallEvents emitted upfront
    assert event_types[2] is ToolCallEvent
    assert event_types[3] is ToolCallEvent
    tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    assert {e.tool_call_id for e in tool_call_events} == {"call_p1", "call_p2"}
    # Both ToolResultEvents present
    tool_result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_result_events) == 2
    assert {e.tool_call_id for e in tool_result_events} == {"call_p1", "call_p2"}
    for tool_result in tool_result_events:
        assert tool_result.error is None
        assert tool_result.skipped is False
        assert tool_result.result is not None
    # Final assistant message
    assert event_types[-1] is AssistantEvent
    assert isinstance(events[-1], AssistantEvent)
    assert events[-1].content == "Both done."
    # Verify conversation history has both tool responses
    tool_msgs = [m for m in agent_loop.messages if m.role == Role.tool]
    assert {m.tool_call_id for m in tool_msgs} == {"call_p1", "call_p2"}
    assert agent_loop.stats.tool_calls_succeeded == 2

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 2


@pytest.mark.asyncio
async def test_parallel_tool_calls_with_approval_callback(
    telemetry_events: list[dict],
) -> None:
    """Two parallel tool calls requiring approval should both succeed when approved."""
    approval_calls: list[str] = []

    async def approval_callback(
        tool_name: str, _args: BaseModel, tool_call_id: str, _rp: list | None = None
    ) -> tuple[ApprovalResponse, str | None]:
        approval_calls.append(tool_call_id)
        return (ApprovalResponse.YES, None)

    tool_call_1 = make_todo_tool_call("call_a1", index=0)
    tool_call_2 = make_todo_tool_call("call_a2", index=1)
    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        approval_callback=approval_callback,
        backend=FakeBackend([
            [
                mock_llm_chunk(
                    content="Checking two.", tool_calls=[tool_call_1, tool_call_2]
                )
            ],
            [mock_llm_chunk(content="Both approved and done.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Check two things")

    # Both should have been approved
    assert set(approval_calls) == {"call_a1", "call_a2"}
    tool_result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_result_events) == 2
    for tool_result in tool_result_events:
        assert tool_result.error is None
        assert tool_result.skipped is False
        assert tool_result.result is not None
    assert agent_loop.stats.tool_calls_agreed == 2
    assert agent_loop.stats.tool_calls_succeeded == 2


@pytest.mark.asyncio
async def test_parallel_approvals_can_run_concurrently() -> None:
    """The core does not serialize approval callbacks — that is a CLI-layer concern.
    Parallel tool calls may invoke the approval callback concurrently.
    """
    concurrency = 0
    max_concurrency = 0

    async def approval_callback(
        tool_name: str, _args: BaseModel, tool_call_id: str, _rp: list | None = None
    ) -> tuple[ApprovalResponse, str | None]:
        nonlocal concurrency, max_concurrency
        concurrency += 1
        max_concurrency = max(max_concurrency, concurrency)
        await asyncio.sleep(0.01)
        concurrency -= 1
        return (ApprovalResponse.YES, None)

    tool_calls = [make_todo_tool_call(f"call_s{i}", index=i) for i in range(3)]
    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        approval_callback=approval_callback,
        backend=FakeBackend([
            [mock_llm_chunk(content="Three tools.", tool_calls=tool_calls)],
            [mock_llm_chunk(content="All done.")],
        ]),
    )

    await act_and_collect_events(agent_loop, "Go")

    assert max_concurrency > 1
    assert agent_loop.stats.tool_calls_agreed == 3
    assert agent_loop.stats.tool_calls_succeeded == 3


@pytest.mark.asyncio
async def test_parallel_mixed_approval_and_rejection(
    telemetry_events: list[dict],
) -> None:
    """One tool approved, one rejected — both should produce correct events."""

    async def approval_callback(
        tool_name: str, _args: BaseModel, tool_call_id: str, _rp: list | None = None
    ) -> tuple[ApprovalResponse, str | None]:
        if tool_call_id == "call_yes":
            return (ApprovalResponse.YES, None)
        return (ApprovalResponse.NO, "Denied by user")

    tc_yes = make_todo_tool_call("call_yes", index=0)
    tc_no = make_todo_tool_call("call_no", index=1)
    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        approval_callback=approval_callback,
        backend=FakeBackend([
            [mock_llm_chunk(content="Two tools.", tool_calls=[tc_yes, tc_no])],
            [mock_llm_chunk(content="Mixed results.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Go")

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 2
    results_by_id = {e.tool_call_id: e for e in tool_results}
    assert results_by_id["call_yes"].error is None
    assert results_by_id["call_yes"].skipped is False
    assert results_by_id["call_yes"].result is not None
    assert results_by_id["call_no"].skipped is True
    assert results_by_id["call_no"].skip_reason == "Denied by user"
    assert results_by_id["call_no"].cancelled is False
    assert agent_loop.stats.tool_calls_agreed == 1
    assert agent_loop.stats.tool_calls_rejected == 1
    assert agent_loop.stats.tool_calls_succeeded == 1

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 2


@pytest.mark.asyncio
async def test_parallel_three_tools_all_succeed(telemetry_events: list[dict]) -> None:
    """Three parallel tool calls should all complete successfully."""
    tool_calls = [make_todo_tool_call(f"call_t{i}", index=i) for i in range(3)]
    agent_loop = make_agent_loop(
        auto_approve=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Three tools.", tool_calls=tool_calls)],
            [mock_llm_chunk(content="All three done.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Go")

    tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
    tool_result_events = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_call_events) == 3
    assert len(tool_result_events) == 3
    assert {e.tool_call_id for e in tool_call_events} == {
        "call_t0",
        "call_t1",
        "call_t2",
    }
    assert {e.tool_call_id for e in tool_result_events} == {
        "call_t0",
        "call_t1",
        "call_t2",
    }
    for tool_result in tool_result_events:
        assert tool_result.error is None
        assert tool_result.result is not None
    assert agent_loop.stats.tool_calls_succeeded == 3
    tool_msgs = [m for m in agent_loop.messages if m.role == Role.tool]
    assert len(tool_msgs) == 3

    tool_finished = [
        e for e in telemetry_events if e.get("event_name") == "vibe.tool_call_finished"
    ]
    assert len(tool_finished) == 3


@pytest.mark.asyncio
async def test_parallel_one_tool_error_does_not_block_others() -> None:
    """If one parallel tool fails, the other should still succeed."""
    tc_good = make_todo_tool_call("call_good", index=0)
    tc_bad = make_todo_tool_call(
        "call_bad", index=1, arguments='{"action": "invalid_action"}'
    )
    agent_loop = make_agent_loop(
        auto_approve=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Two tools.", tool_calls=[tc_good, tc_bad])],
            [mock_llm_chunk(content="Done.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Go")

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 2
    results_by_id = {e.tool_call_id: e for e in tool_results}
    assert results_by_id["call_good"].error is None
    assert results_by_id["call_good"].result is not None
    assert results_by_id["call_bad"].error is not None
    assert results_by_id["call_bad"].result is None
    assert agent_loop.stats.tool_calls_succeeded == 1
    assert agent_loop.stats.tool_calls_failed == 1


@pytest.mark.asyncio
async def test_parallel_all_rejected_no_callback() -> None:
    """Parallel tools with no approval callback should all be skipped."""
    tc1 = make_todo_tool_call("call_nc1", index=0)
    tc2 = make_todo_tool_call("call_nc2", index=1)
    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.ASK,
        backend=FakeBackend([
            [mock_llm_chunk(content="Two tools.", tool_calls=[tc1, tc2])],
            [mock_llm_chunk(content="Cannot proceed.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Go")

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 2
    for tool_result in tool_results:
        assert tool_result.skipped is True
        assert tool_result.result is None
    assert agent_loop.stats.tool_calls_rejected == 2
    assert agent_loop.stats.tool_calls_succeeded == 0


@pytest.mark.asyncio
async def test_parallel_all_permission_never() -> None:
    """Parallel tools with NEVER permission skip without calling the approval callback."""
    approval_calls: list[str] = []

    async def approval_callback(
        tool_name: str, _args: BaseModel, tool_call_id: str, _rp: list | None = None
    ) -> tuple[ApprovalResponse, str | None]:
        approval_calls.append(tool_call_id)
        return (ApprovalResponse.YES, None)

    tc1 = make_todo_tool_call("call_nv1", index=0)
    tc2 = make_todo_tool_call("call_nv2", index=1)
    agent_loop = make_agent_loop(
        auto_approve=False,
        todo_permission=ToolPermission.NEVER,
        approval_callback=approval_callback,
        backend=FakeBackend([
            [mock_llm_chunk(content="Two tools.", tool_calls=[tc1, tc2])],
            [mock_llm_chunk(content="Both disabled.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Go")

    assert len(approval_calls) == 0
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 2
    for tool_result in tool_results:
        assert tool_result.skipped is True
        assert "permanently disabled" in (tool_result.skip_reason or "").lower()
    assert agent_loop.stats.tool_calls_rejected == 2


@pytest.mark.asyncio
async def test_parallel_tool_call_events_emitted_before_results() -> None:
    """All ToolCallEvents must appear before any ToolResultEvent in the event stream."""
    tool_calls = [make_todo_tool_call(f"call_o{i}", index=i) for i in range(3)]
    agent_loop = make_agent_loop(
        auto_approve=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Three tools.", tool_calls=tool_calls)],
            [mock_llm_chunk(content="Done.")],
        ]),
    )

    events = await act_and_collect_events(agent_loop, "Go")

    last_call_idx = max(i for i, e in enumerate(events) if isinstance(e, ToolCallEvent))
    first_result_idx = min(
        i for i, e in enumerate(events) if isinstance(e, ToolResultEvent)
    )
    assert last_call_idx < first_result_idx


@pytest.mark.asyncio
async def test_parallel_conversation_history_has_all_tool_messages() -> None:
    """All parallel tool results must appear in the conversation messages."""
    tool_calls = [make_todo_tool_call(f"call_h{i}", index=i) for i in range(4)]
    agent_loop = make_agent_loop(
        auto_approve=True,
        backend=FakeBackend([
            [mock_llm_chunk(content="Four tools.", tool_calls=tool_calls)],
            [mock_llm_chunk(content="All four done.")],
        ]),
    )

    await act_and_collect_events(agent_loop, "Go")

    tool_msgs = [m for m in agent_loop.messages if m.role == Role.tool]
    assert {m.tool_call_id for m in tool_msgs} == {
        "call_h0",
        "call_h1",
        "call_h2",
        "call_h3",
    }
    assert agent_loop.stats.tool_calls_succeeded == 4
