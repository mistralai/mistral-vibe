from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from mistralai_vibe_local_harness.protocol import (
    JsonObject,
    JsonSchema,
    JsonValue,
    RustEvent,
    RustProvidedToolCall,
    RustProvidedToolCallAction,
    RustToolFailedEvent,
    RustToolSucceededEvent,
)
import pytest

from vibe.app_server._provided_tools import (
    TODO_TOOL_NAME,
    VIBE_PROVIDED_TOOL_MODES,
    VIBE_PROVIDED_TOOL_NAMES,
    VIBE_TOOL_GROUP,
    VibeProvidedTools,
    resolved_max_todos,
    vibe_tool_groups,
)
from vibe.app_server._unified_scratchpad import SCRATCHPAD_TOOL_NAME, scratchpad_dir
from vibe.core.tools.builtins.todo import TodoConfig

_Executor = Callable[[RustProvidedToolCallAction], Awaitable[RustEvent]]


def _executor(
    root: Path,
    session_id: str = "session-1",
    *,
    tools: VibeProvidedTools | None = None,
    max_todos: Callable[[], int] | None = None,
) -> _Executor:
    holder = tools if tools is not None else VibeProvidedTools()
    return holder.executor_factory(
        root, max_todos=max_todos or (lambda: TodoConfig().max_todos)
    )(session_id)


def _call(
    action_id: str, *, tool_name: str = "todo", **arguments: JsonValue
) -> RustProvidedToolCallAction:
    return RustProvidedToolCallAction(
        action_id=action_id,
        turn_id="turn-1",
        call_id=f"call-{action_id}",
        call=RustProvidedToolCall(
            group_name=VIBE_TOOL_GROUP, tool_name=tool_name, arguments=arguments
        ),
    )


def _scratchpad(action_id: str, **arguments: JsonValue) -> RustProvidedToolCallAction:
    return _call(action_id, tool_name=SCRATCHPAD_TOOL_NAME, **arguments)


def _item(todo_id: str, content: str) -> JsonValue:
    return {"id": todo_id, "content": content}


def _schema_property(schema: JsonSchema, name: str) -> JsonObject:
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    prop = properties[name]
    assert isinstance(prop, dict)
    return prop


def test_the_vibe_group_declares_todo_when_the_legacy_tool_is_available() -> None:
    groups = vibe_tool_groups({"todo", "bash"})

    assert len(groups) == 1
    assert groups[0].name == VIBE_TOOL_GROUP
    assert [tool.name for tool in groups[0].tools] == ["todo", SCRATCHPAD_TOOL_NAME]
    todo = groups[0].tools[0]
    assert todo.exposure == "direct"
    assert todo.description
    # The Literal reaches the model as an enum, which is the point of tightening it.
    assert _schema_property(todo.input_schema, "action")["enum"] == ["read", "write"]


def test_the_scratchpad_survives_todo_being_switched_off() -> None:
    groups = vibe_tool_groups({"bash"})

    # It is gated on nothing: there is no legacy scratchpad tool for a config to
    # disable, so the group is declared for the scratchpad alone.
    assert [tool.name for tool in groups[0].tools] == [SCRATCHPAD_TOOL_NAME]
    scratchpad = groups[0].tools[0]
    assert scratchpad.exposure == "direct"
    assert _schema_property(scratchpad.input_schema, "action")["enum"] == [
        "read",
        "write",
        "list",
    ]


@pytest.mark.asyncio
async def test_a_write_is_visible_to_the_next_read(tmp_path: Path) -> None:
    execute = _executor(tmp_path)

    written = await execute(
        _call("a1", action="write", todos=[_item("1", "Task A"), _item("2", "Task B")])
    )
    read = await execute(_call("a2", action="read"))

    assert isinstance(written, RustToolSucceededEvent)
    assert written.result.structured_content == {
        "verb": "Updated",
        "todos": [
            {"id": "1", "content": "Task A", "status": "pending", "priority": "medium"},
            {"id": "2", "content": "Task B", "status": "pending", "priority": "medium"},
        ],
        "total_count": 2,
        "message": "Updated 2 todos",
    }
    assert isinstance(read, RustToolSucceededEvent)
    assert isinstance(read.result.structured_content, dict)
    assert read.result.structured_content["verb"] == "Retrieved"
    assert read.result.structured_content["total_count"] == 2


@pytest.mark.asyncio
async def test_each_session_holds_its_own_list(tmp_path: Path) -> None:
    tools = VibeProvidedTools()
    first = _executor(tmp_path, "session-1", tools=tools)
    second = _executor(tmp_path, "session-2", tools=tools)

    await first(_call("a1", action="write", todos=[_item("1", "Task A")]))
    read = await second(_call("a2", action="read"))

    assert isinstance(read, RustToolSucceededEvent)
    assert isinstance(read.result.structured_content, dict)
    assert read.result.structured_content["total_count"] == 0


@pytest.mark.asyncio
async def test_duplicate_ids_fail_the_call_without_touching_the_list(
    tmp_path: Path,
) -> None:
    execute = _executor(tmp_path)

    await execute(_call("a1", action="write", todos=[_item("1", "Task A")]))
    rejected = await execute(
        _call("a2", action="write", todos=[_item("2", "B"), _item("2", "C")])
    )
    read = await execute(_call("a3", action="read"))

    assert isinstance(rejected, RustToolFailedEvent)
    assert "unique" in rejected.result.error.message.lower()
    assert rejected.result.error.retryable is False
    assert isinstance(read, RustToolSucceededEvent)
    assert isinstance(read.result.structured_content, dict)
    assert read.result.structured_content["todos"] == [
        {"id": "1", "content": "Task A", "status": "pending", "priority": "medium"}
    ]


@pytest.mark.asyncio
async def test_a_list_over_the_cap_fails_the_call(tmp_path: Path) -> None:
    execute = _executor(tmp_path)
    max_todos = TodoConfig().max_todos

    rejected = await execute(
        _call(
            "a1",
            action="write",
            todos=[_item(str(index), "Task") for index in range(max_todos + 1)],
        )
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert str(max_todos) in rejected.result.error.message


@pytest.mark.asyncio
async def test_an_unknown_action_fails_the_call(tmp_path: Path) -> None:
    execute = _executor(tmp_path)

    rejected = await execute(_call("a1", action="delete"))

    assert isinstance(rejected, RustToolFailedEvent)
    assert "'read' or 'write'" in rejected.result.error.message


@pytest.mark.asyncio
async def test_an_unknown_tool_in_the_group_fails_the_call(tmp_path: Path) -> None:
    execute = _executor(tmp_path)

    rejected = await execute(_call("a1", tool_name="notepad", action="read"))

    assert isinstance(rejected, RustToolFailedEvent)
    assert "notepad" in rejected.result.error.message


@pytest.mark.asyncio
async def test_a_scratchpad_write_lands_in_the_sessions_own_directory(
    tmp_path: Path,
) -> None:
    execute = _executor(tmp_path)

    written = await execute(
        _scratchpad("a1", action="write", path="notes/plan.md", content="ship it")
    )
    read = await execute(_scratchpad("a2", action="read", path="notes/plan.md"))

    assert isinstance(written, RustToolSucceededEvent)
    assert written.result.structured_content == {
        "verb": "Wrote",
        "path": "notes/plan.md",
        "content": None,
        "files": [],
        "message": "Wrote notes/plan.md",
    }
    # The hook reads the directory rather than the executor's state, so the write
    # has to be on disk where `scratchpad_dir` will look for it.
    assert (
        scratchpad_dir(tmp_path, "session-1") / "notes" / "plan.md"
    ).read_text() == "ship it"
    assert isinstance(read, RustToolSucceededEvent)
    assert isinstance(read.result.structured_content, dict)
    assert read.result.structured_content["content"] == "ship it"


@pytest.mark.asyncio
async def test_each_session_holds_its_own_scratchpad(tmp_path: Path) -> None:
    tools = VibeProvidedTools()
    first = _executor(tmp_path, "session-1", tools=tools)
    second = _executor(tmp_path, "session-2", tools=tools)

    await first(_scratchpad("a1", action="write", path="plan.md", content="ship it"))
    listed = await second(_scratchpad("a2", action="list"))

    assert isinstance(listed, RustToolSucceededEvent)
    assert isinstance(listed.result.structured_content, dict)
    assert listed.result.structured_content["files"] == []


@pytest.mark.asyncio
async def test_a_scratchpad_path_escaping_the_directory_fails_the_call(
    tmp_path: Path,
) -> None:
    execute = _executor(tmp_path)

    rejected = await execute(
        _scratchpad("a1", action="write", path="../../escaped.md", content="oops")
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert "escapes" in rejected.result.error.message
    assert not (tmp_path / "escaped.md").exists()


@pytest.mark.asyncio
async def test_a_scratchpad_write_without_content_fails_the_call(
    tmp_path: Path,
) -> None:
    execute = _executor(tmp_path)

    rejected = await execute(_scratchpad("a1", action="write", path="plan.md"))

    assert isinstance(rejected, RustToolFailedEvent)
    assert "'content' is required" in rejected.result.error.message


def test_the_scratchpad_obeys_the_tool_filters() -> None:
    disabled = vibe_tool_groups({"todo"}, disabled_tools=[SCRATCHPAD_TOOL_NAME])
    unlisted = vibe_tool_groups({"todo"}, enabled_tools=["bash"])
    listed = vibe_tool_groups({"todo"}, enabled_tools=["unified_harness_*"])

    assert [tool.name for tool in disabled[0].tools] == [TODO_TOOL_NAME]
    assert [tool.name for tool in unlisted[0].tools] == [TODO_TOOL_NAME]
    assert [tool.name for tool in listed[0].tools] == [
        TODO_TOOL_NAME,
        SCRATCHPAD_TOOL_NAME,
    ]


def test_the_group_disappears_when_both_tools_are_filtered_out() -> None:
    assert vibe_tool_groups(set(), disabled_tools=[SCRATCHPAD_TOOL_NAME]) == []


def test_todo_is_routed_to_the_name_its_permission_is_configured_under() -> None:
    # The resolver reads `[tools.todo]`, so the route has to publish that name; the
    # scratchpad has no such entry, which is why only todo is registered.
    assert VIBE_PROVIDED_TOOL_NAMES == {f"{VIBE_TOOL_GROUP}.{TODO_TOOL_NAME}": "todo"}
    assert VIBE_PROVIDED_TOOL_MODES == {
        TODO_TOOL_NAME: "ask",
        SCRATCHPAD_TOOL_NAME: "allow",
    }


@pytest.mark.asyncio
async def test_forgetting_a_sessions_todos_reaches_the_live_executor(
    tmp_path: Path,
) -> None:
    tools = VibeProvidedTools()
    execute = _executor(tmp_path, "session-1", tools=tools)
    other = _executor(tmp_path, "session-2", tools=tools)

    await execute(_call("a1", action="write", todos=[_item("1", "Task A")]))
    await other(_call("a2", action="write", todos=[_item("1", "Task B")]))
    tools.forget_todos("session-1")
    read = await execute(_call("a3", action="read"))
    untouched = await other(_call("a4", action="read"))

    assert isinstance(read, RustToolSucceededEvent)
    assert isinstance(read.result.structured_content, dict)
    assert read.result.structured_content["total_count"] == 0
    assert isinstance(untouched, RustToolSucceededEvent)
    assert isinstance(untouched.result.structured_content, dict)
    assert untouched.result.structured_content["total_count"] == 1


@pytest.mark.asyncio
async def test_an_adopting_holder_forgets_the_lists_the_live_executor_holds(
    tmp_path: Path,
) -> None:
    previous = VibeProvidedTools()
    execute = _executor(tmp_path, "session-1", tools=previous)
    await execute(_call("a1", action="write", todos=[_item("1", "Task A")]))

    # A context swap hands the session to a fresh holder while its executor keeps
    # closing over the list the old one handed out.
    incoming = VibeProvidedTools()
    incoming.adopt(previous)
    incoming.forget_todos("session-1")
    read = await execute(_call("a2", action="read"))

    assert isinstance(read, RustToolSucceededEvent)
    assert isinstance(read.result.structured_content, dict)
    assert read.result.structured_content["total_count"] == 0


@pytest.mark.asyncio
async def test_the_cap_is_read_per_call(tmp_path: Path) -> None:
    cap = 1
    execute = _executor(tmp_path, max_todos=lambda: cap)

    rejected = await execute(
        _call("a1", action="write", todos=[_item("1", "A"), _item("2", "B")])
    )
    cap = 2
    accepted = await execute(
        _call("a2", action="write", todos=[_item("1", "A"), _item("2", "B")])
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert isinstance(accepted, RustToolSucceededEvent)


def test_the_cap_falls_back_to_the_tools_own_default() -> None:
    default = TodoConfig().max_todos

    assert resolved_max_todos(None) == default
    assert resolved_max_todos({}) == default
    assert resolved_max_todos({"max_todos": 3}) == 3
    # `/config` can leave nonsense behind; the tool must not stop working over it.
    assert resolved_max_todos({"max_todos": "many"}) == default
