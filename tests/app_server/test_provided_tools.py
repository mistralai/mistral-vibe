from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.protocol import (
    JsonObject,
    JsonSchema,
    RustEvent,
    RustProvidedToolCall,
    RustProvidedToolCallAction,
    RustTextContentBlock,
    RustToolFailedEvent,
    RustToolGroupDefinition,
    RustToolSucceededEvent,
)
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._cron import CRON_TOOL_NAME, CronArgs
from vibe.app_server._provided_tools import (
    TODO_TOOL_NAME,
    VIBE_PROVIDED_TOOL_MODES,
    VIBE_PROVIDED_TOOL_NAMES,
    VIBE_TOOL_GROUP,
    VibeProvidedTools,
    resolved_max_todos,
    vibe_tool_groups,
)
from vibe.app_server._session_spawn import (
    START_SESSION_TOOL_NAME,
    SessionOriginResolver,
    SessionSettings,
    SessionSpawner,
    SessionSpawnError,
    SessionSpawnOrigin,
    SpawnModel,
)
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    UnifiedScheduledLoops,
)
from vibe.app_server._unified_scratchpad import SCRATCHPAD_TOOL_NAME, scratchpad_dir
from vibe.app_server.protocol import (
    SessionSpawnCheckout,
    SessionSpawnDirectory,
    SessionSpawnManagedWorktree,
    SessionSpawnNewWorktree,
    SessionSpawnParams,
    SessionSpawnResponse,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.loop import MAX_LOOPS_PER_SESSION, LoopError
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.builtins.todo import TodoConfig
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.permissions import PermissionStore

_Executor = Callable[[RustProvidedToolCallAction], Awaitable[RustEvent]]


def _executor(
    root: Path,
    session_id: str = "session-1",
    *,
    tools: VibeProvidedTools | None = None,
    max_todos: Callable[[], int] | None = None,
    scheduled_loops: Callable[[str], UnifiedScheduledLoops] | None = None,
    session_spawner: SessionSpawner | None = None,
    session_origin: SessionOriginResolver | None = None,
) -> _Executor:
    holder = tools if tools is not None else VibeProvidedTools()
    return holder.executor_factory(
        root,
        max_todos=max_todos or (lambda: TodoConfig().max_todos),
        scheduled_loops=scheduled_loops,
        session_spawner=session_spawner,
        session_origin=session_origin or _parent_in_a_managed_worktree,
    )(session_id)


_PARENT_SETTINGS = SessionSettings(
    model="medium", reasoning_effort="low", approval_policy="default"
)
_MODELS = (
    SpawnModel(alias="medium", display_name=None, levels=("low", "high")),
    SpawnModel(
        alias="claude-opus-5", display_name="Claude Opus 5", levels=("off", "max")
    ),
)


def _parent_in_a_managed_worktree(_session_id: str) -> SessionSpawnOrigin:
    return SessionSpawnOrigin(
        checkout=SessionSpawnManagedWorktree(path="/repo/.worktrees/parent"),
        settings=_PARENT_SETTINGS,
        models=_MODELS,
    )


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
    assert [tool.name for tool in groups[0].tools] == [
        "todo",
        SCRATCHPAD_TOOL_NAME,
        CRON_TOOL_NAME,
    ]
    todo = groups[0].tools[0]
    assert todo.exposure == "direct_and_programmatic"
    assert "tools.vibe.todo" in todo.description
    assert "after each step" in todo.description
    # The Literal reaches the model as an enum, which is the point of tightening it.
    assert _schema_property(todo.input_schema, "action")["enum"] == ["read", "write"]


def test_the_scratchpad_survives_todo_being_switched_off() -> None:
    groups = vibe_tool_groups({"bash"})

    assert [tool.name for tool in groups[0].tools] == [
        SCRATCHPAD_TOOL_NAME,
        CRON_TOOL_NAME,
    ]
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
async def test_a_tool_result_reaches_the_model_without_unicode_escapes(
    tmp_path: Path,
) -> None:
    execute = _executor(tmp_path)

    written = await execute(_call("a1", action="write", todos=[_item("1", "مرحبا")]))

    assert isinstance(written, RustToolSucceededEvent)
    [block] = written.result.content
    assert isinstance(block, RustTextContentBlock)
    assert '"content": "مرحبا"' in block.text


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
    # Notes live in the session's own directory, so they share its lifetime: the
    # write has to land where `scratchpad_dir` points.
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

    assert [tool.name for tool in disabled[0].tools] == [TODO_TOOL_NAME, CRON_TOOL_NAME]
    assert [tool.name for tool in unlisted[0].tools] == [TODO_TOOL_NAME]
    assert [tool.name for tool in listed[0].tools] == [
        TODO_TOOL_NAME,
        SCRATCHPAD_TOOL_NAME,
    ]


def test_the_group_disappears_when_all_tools_are_filtered_out() -> None:
    assert (
        vibe_tool_groups(set(), disabled_tools=[SCRATCHPAD_TOOL_NAME, CRON_TOOL_NAME])
        == []
    )


def test_todo_is_routed_to_the_name_its_permission_is_configured_under() -> None:
    assert VIBE_PROVIDED_TOOL_NAMES == {
        f"{VIBE_TOOL_GROUP}.{TODO_TOOL_NAME}": "todo",
        f"{VIBE_TOOL_GROUP}.{CRON_TOOL_NAME}": "cron",
        f"{VIBE_TOOL_GROUP}.{START_SESSION_TOOL_NAME}": "start_session",
        f"{VIBE_TOOL_GROUP}.list_sessions": "list_sessions",
        f"{VIBE_TOOL_GROUP}.read_session": "read_session",
        f"{VIBE_TOOL_GROUP}.send_to_session": "send_to_session",
        f"{VIBE_TOOL_GROUP}.archive_sessions": "archive_sessions",
    }
    assert VIBE_PROVIDED_TOOL_MODES == {
        TODO_TOOL_NAME: "ask",
        CRON_TOOL_NAME: "ask",
        START_SESSION_TOOL_NAME: "ask",
        "list_sessions": "ask",
        "read_session": "ask",
        "send_to_session": "ask",
        "archive_sessions": "ask",
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


def test_cron_declares_discriminated_actions_and_structured_results() -> None:
    tool = next(
        tool for tool in vibe_tool_groups(set())[0].tools if tool.name == "cron"
    )

    assert tool.exposure == "direct"
    schema = CronArgs.model_json_schema()
    assert isinstance(schema, dict)
    assert isinstance(tool.input_schema, dict)
    assert tool.input_schema["type"] == "object"
    assert not {"oneOf", "allOf", "anyOf"} & tool.input_schema.keys()
    assert tool.input_schema["required"] == ["action"]
    assert _schema_property(tool.input_schema, "action")["enum"] == [
        "schedule",
        "schedule_cron",
        "list",
        "cancel",
        "clear",
    ]
    assert schema["discriminator"]["propertyName"] == "action"
    assert set(schema["discriminator"]["mapping"]) == {
        "schedule",
        "schedule_cron",
        "list",
        "cancel",
        "clear",
    }
    assert len(schema["oneOf"]) == 5
    variants = schema["$defs"]
    assert variants["ScheduleArgs"]["required"] == [
        "action",
        "interval_seconds",
        "prompt",
    ]
    interval = variants["ScheduleArgs"]["properties"]["interval_seconds"]
    assert interval["type"] == "integer"
    assert interval["minimum"] == 30
    assert _schema_property(tool.input_schema, "interval_seconds") == interval
    assert variants["ScheduleCronArgs"]["required"] == ["action", "cron", "prompt"]
    assert variants["CancelArgs"]["required"] == ["action", "id"]
    assert tool.output_schema is not None
    assert _schema_property(tool.output_schema, "loops")["type"] == "array"
    assert _schema_property(tool.output_schema, "verb")["type"] == "string"
    assert _schema_property(tool.output_schema, "message")["type"] == "string"
    for term in (
        "50",
        "persisted",
        "live",
        "idle",
        "local timezone",
        "not caught up",
        "human-readable",
        "short title",
        "line break",
        "first line",
    ):
        assert term in tool.description
    # The prompt field tells the model to write a short title on the first
    # line, then a line break, then the full instruction shown to the user
    # as-is; the first line is displayed in the UI as the loop's name.
    prompt_description = variants["ScheduleArgs"]["properties"]["prompt"]["description"]
    assert "human-readable" in prompt_description
    assert "short title" in prompt_description
    assert "line break" in prompt_description
    assert "first line" in prompt_description


@pytest.mark.parametrize(
    ("enabled", "disabled", "expected"),
    [
        ([], [], True),
        (["cron"], [], True),
        (["cr*"], [], True),
        (["bash"], [], False),
        ([], ["cron"], False),
        ([], ["cr*"], False),
        (["cron"], ["cron"], False),
    ],
)
def test_cron_obeys_tool_filters(enabled, disabled, expected) -> None:
    groups = vibe_tool_groups(set(), enabled_tools=enabled, disabled_tools=disabled)
    assert (
        any(tool.name == "cron" for group in groups for tool in group.tools) is expected
    )


def _cron(action_id: str, **arguments: JsonValue) -> RustProvidedToolCallAction:
    return _call(action_id, tool_name=CRON_TOOL_NAME, **arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [True, False])
async def test_cron_actions_use_the_session_store(
    tmp_path: Path, persistent: bool
) -> None:
    path = tmp_path / "loops.json"
    store = UnifiedScheduledLoops(path, persistent=lambda: persistent)
    execute = _executor(tmp_path, scheduled_loops=lambda _: store)

    interval = await execute(
        _cron("interval", action="schedule", interval_seconds=30, prompt="check tests")
    )
    cron = await execute(
        _cron("cron", action="schedule_cron", cron="0 9 * * 1-5", prompt="check build")
    )
    loops = await store.list()
    assert len(loops) == 2
    for event, loop in zip((interval, cron), loops, strict=True):
        assert isinstance(event, RustToolSucceededEvent)
        assert event.result.structured_content == {
            "verb": "Scheduled",
            "loops": [loop.model_dump(mode="json")],
            "message": f"Scheduled loop {loop.id}",
        }
        assert event.result.content
    assert loops[0].interval_seconds == 30
    assert loops[1].cron == "0 9 * * 1-5"
    assert path.exists() is persistent
    restored = UnifiedScheduledLoops(path, persistent=lambda: True)
    if persistent:
        await restored.restore()
        assert await restored.list() == loops

    listed = await execute(_cron("list", action="list"))
    assert isinstance(listed, RustToolSucceededEvent)
    assert listed.result.structured_content == {
        "verb": "Listed",
        "loops": [loop.model_dump(mode="json") for loop in loops],
        "message": "Listed 2 scheduled loops",
    }

    cancelled = await execute(_cron("cancel", action="cancel", id=loops[0].id))
    assert isinstance(cancelled, RustToolSucceededEvent)
    assert cancelled.result.structured_content == {
        "verb": "Cancelled",
        "loops": [loops[0].model_dump(mode="json")],
        "message": f"Cancelled loop {loops[0].id}",
    }
    assert await store.list() == [loops[1]]

    cleared = await execute(_cron("clear", action="clear"))
    assert isinstance(cleared, RustToolSucceededEvent)
    assert cleared.result.structured_content == {
        "verb": "Cleared",
        "loops": [],
        "message": "Cleared 1 scheduled loops",
        "cleared_count": 1,
    }
    assert await store.list() == []
    if persistent:
        await restored.restore()
        assert await restored.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"action": "unknown"},
        {"action": "schedule", "prompt": "check"},
        *[
            {"action": "schedule", "interval_seconds": interval, "prompt": "check"}
            for interval in (29, 30.5, 30.0, "30", True, None)
        ],
        {"action": "schedule", "interval_seconds": 30},
        {"action": "schedule", "interval_seconds": 30, "prompt": ""},
        {"action": "schedule", "interval_seconds": 30, "prompt": "   "},
        {"action": "schedule", "interval_seconds": 30, "prompt": "/loop list"},
        {"action": "schedule_cron", "prompt": "check"},
        *[
            {"action": "schedule_cron", "cron": cron, "prompt": "check"}
            for cron in ("invalid", "* * * *", "* * * * * *", "@daily", "60 * * * *")
        ],
        {"action": "cancel"},
        {"action": "cancel", "id": "missing"},
        {"action": "cancel", "id": ""},
        {"action": "list", "interval_seconds": 30},
    ],
)
async def test_invalid_cron_calls_fail_without_mutating_the_store(
    tmp_path: Path, arguments: dict[str, JsonValue]
) -> None:
    store = UnifiedScheduledLoops(tmp_path / "loops.json", persistent=lambda: False)
    execute = _executor(tmp_path, scheduled_loops=lambda _: store)
    existing = await store.create_interval(60, "keep me")

    rejected = await execute(_cron("invalid", **arguments))

    assert isinstance(rejected, RustToolFailedEvent)
    assert rejected.action_id == "invalid"
    assert rejected.call_id == "call-invalid"
    assert rejected.result.error.code == "tool_failed"
    assert rejected.result.error.message
    assert rejected.result.error.retryable is False
    assert await store.list() == [existing]


@pytest.mark.asyncio
async def test_cron_limit_is_a_correctable_tool_failure(tmp_path: Path) -> None:
    store = UnifiedScheduledLoops(tmp_path / "loops.json", persistent=lambda: False)
    execute = _executor(tmp_path, scheduled_loops=lambda _: store)
    for _ in range(MAX_LOOPS_PER_SESSION):
        await store.create_interval(30, "check")

    rejected = await execute(
        _cron("overflow", action="schedule", interval_seconds=30, prompt="check")
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert "50" in rejected.result.error.message
    assert len(await store.list()) == MAX_LOOPS_PER_SESSION


@pytest.mark.asyncio
async def test_cron_without_a_backend_is_a_correctable_tool_failure(
    tmp_path: Path,
) -> None:
    rejected = await _executor(tmp_path)(_cron("unavailable", action="list"))

    assert isinstance(rejected, RustToolFailedEvent)
    assert "not available" in rejected.result.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, LoopError, ScheduledLoopStoreError])
async def test_cron_backend_resolution_failures_are_correctable(
    tmp_path: Path, error_type: type[Exception]
) -> None:
    def unavailable(session_id: str) -> UnifiedScheduledLoops:
        raise error_type(f"No live backend for {session_id}")

    execute = _executor(tmp_path, scheduled_loops=unavailable)
    rejected = await execute(_cron("unavailable", action="list"))

    assert isinstance(rejected, RustToolFailedEvent)
    assert "No live backend for session-1" in rejected.result.error.message
    assert isinstance(
        await execute(_call("todo", action="read")), RustToolSucceededEvent
    )
    assert isinstance(
        await execute(_scratchpad("scratchpad", action="list")), RustToolSucceededEvent
    )


@pytest.mark.asyncio
async def test_cron_persistence_failure_reaches_the_model(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied")
    store = UnifiedScheduledLoops(parent / "loops.json", persistent=lambda: True)
    execute = _executor(tmp_path, scheduled_loops=lambda _: store)

    rejected = await execute(
        _cron("persist", action="schedule", interval_seconds=30, prompt="check")
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert "persist" in rejected.result.error.message.lower()
    assert await store.list() == []


@pytest.mark.asyncio
async def test_cron_resolves_the_calling_session_backend_on_every_call(
    tmp_path: Path,
) -> None:
    store = UnifiedScheduledLoops(tmp_path / "first.json", persistent=lambda: False)
    sessions: list[str] = []

    def resolve(session_id: str) -> UnifiedScheduledLoops:
        sessions.append(session_id)
        return store

    execute = _executor(tmp_path, "child-session", scheduled_loops=resolve)
    assert sessions == []
    first = await execute(
        _cron("first", action="schedule", interval_seconds=30, prompt="first")
    )
    assert isinstance(first, RustToolSucceededEvent)
    assert len(await store.list()) == 1

    store = UnifiedScheduledLoops(tmp_path / "second.json", persistent=lambda: False)
    listed = await execute(_cron("second", action="list"))
    assert isinstance(listed, RustToolSucceededEvent)
    assert isinstance(listed.result.structured_content, dict)
    assert listed.result.structured_content["loops"] == []
    assert sessions == ["child-session", "child-session"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permission", "decision"),
    [(None, "allow"), ("ask", "ask"), ("always", "allow"), ("never", "deny")],
)
@pytest.mark.parametrize(
    "session_permission", [None, ToolPermission.ASK, ToolPermission.NEVER]
)
async def test_cron_approval_mode_honors_config(
    tmp_path: Path,
    permission: str | None,
    decision: str,
    session_permission: ToolPermission | None,
) -> None:
    config = build_test_vibe_config(
        tools={} if permission is None else {"cron": {"permission": permission}}
    )
    orchestrator = FakeConfigOrchestrator(config)
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=tmp_path,
        harness_files=HarnessFilesManager().for_session(tmp_path),
        permission_getter=store.get_tool_permission,
    )
    resolver = UnifiedPermissionResolver(manager, store, orchestrator)
    resolver.provided_names.register(VIBE_TOOL_GROUP, VIBE_PROVIDED_TOOL_NAMES)

    if session_permission is not None:
        store.set_tool_permission(CRON_TOOL_NAME, session_permission)
        decision = "deny" if session_permission is ToolPermission.NEVER else "ask"

    assert VIBE_PROVIDED_TOOL_MODES[CRON_TOOL_NAME] == "ask"
    outcome = await resolver.resolve("vibe.cron", {"action": "list"})
    assert outcome.decision == decision


class _FakeSpawner:
    def __init__(
        self,
        answer: Callable[[SessionSpawnParams], Awaitable[SessionSpawnResponse]]
        | None = None,
    ) -> None:
        self.requests: list[SessionSpawnParams] = []
        self._answer = answer

    async def spawn_session(self, params: SessionSpawnParams) -> SessionSpawnResponse:
        self.requests.append(params)
        if self._answer is not None:
            return await self._answer(params)
        return SessionSpawnResponse(session_id="spawned-1", title=params.title)


def _start_session(
    action_id: str, **arguments: JsonValue
) -> RustProvidedToolCallAction:
    return _call(action_id, tool_name=START_SESSION_TOOL_NAME, **arguments)


def _tool_names(groups: list[RustToolGroupDefinition]) -> list[str]:
    return [tool.name for group in groups for tool in group.tools]


def test_start_session_is_offered_only_to_a_client_that_can_spawn() -> None:
    assert START_SESSION_TOOL_NAME not in _tool_names(vibe_tool_groups({"bash"}))

    groups = vibe_tool_groups({"bash"}, session_spawn=_MODELS)

    start = next(
        tool for tool in groups[0].tools if tool.name == START_SESSION_TOOL_NAME
    )
    assert start.exposure == "direct"
    schema = start.input_schema
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {
        "prompt",
        "title",
        "placement",
        "model",
        "reasoning_effort",
    }
    assert schema["required"] == ["prompt", "placement"]
    assert schema["additionalProperties"] is False
    assert "$defs" not in schema
    # Named, so a user's "opus" can be matched to its alias.
    assert "medium; claude-opus-5 (Claude Opus 5)" in str(
        _schema_property(schema, "model")["description"]
    )
    assert "medium: low, high; claude-opus-5: off, max" in str(
        _schema_property(schema, "reasoning_effort")["description"]
    )
    assert _schema_property(schema, "placement")["enum"] == [
        "current_checkout",
        "new_worktree",
    ]


def test_start_session_obeys_tool_filters() -> None:
    groups = vibe_tool_groups(
        {"bash"}, session_spawn=_MODELS, disabled_tools=[START_SESSION_TOOL_NAME]
    )

    assert START_SESSION_TOOL_NAME not in _tool_names(groups)


@pytest.mark.asyncio
async def test_start_session_asks_the_client_for_the_calling_session(
    tmp_path: Path,
) -> None:
    spawner = _FakeSpawner()
    execute = _executor(tmp_path, "parent-session", session_spawner=spawner)

    started = await execute(
        _start_session(
            "start",
            prompt="Fix the flaky test",
            title="Flaky test",
            placement="new_worktree",
        )
    )

    assert isinstance(started, RustToolSucceededEvent)
    assert started.result.structured_content == {
        "session_id": "spawned-1",
        "title": "Flaky test",
        "url": None,
        "message": 'Started "Flaky test"',
    }
    [request] = spawner.requests
    assert request.model_dump(exclude={"idempotency_key"}) == SessionSpawnParams(
        parent_session_id="parent-session",
        prompt="Fix the flaky test",
        title="Flaky test",
        checkout=SessionSpawnNewWorktree(),
        idempotency_key="unused",
        model=_PARENT_SETTINGS.model,
        reasoning_effort=_PARENT_SETTINGS.reasoning_effort,
        approval_policy=_PARENT_SETTINGS.approval_policy,
    ).model_dump(exclude={"idempotency_key"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkout",
    [
        SessionSpawnManagedWorktree(path="/repo/.worktrees/parent"),
        SessionSpawnDirectory(path="/repo-linked-checkout"),
    ],
)
async def test_start_session_in_the_current_checkout_runs_where_the_caller_does(
    tmp_path: Path, checkout: SessionSpawnCheckout
) -> None:
    spawner = _FakeSpawner()

    def origin(_session_id: str) -> SessionSpawnOrigin:
        return SessionSpawnOrigin(
            checkout=checkout, settings=_PARENT_SETTINGS, models=_MODELS
        )

    started = await _executor(tmp_path, session_spawner=spawner, session_origin=origin)(
        _start_session("start", prompt="Fix it", placement="current_checkout")
    )

    assert isinstance(started, RustToolSucceededEvent)
    [request] = spawner.requests
    assert request.checkout == checkout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asked", "sent"),
    [
        # Another model at its own default level: the parent's may not exist.
        ({"model": "claude-opus-5"}, ("claude-opus-5", None)),
        (
            {"model": "claude-opus-5", "reasoning_effort": "max"},
            ("claude-opus-5", "max"),
        ),
        # The parent's model keeps the parent's level unless told otherwise.
        ({"model": "medium"}, ("medium", "low")),
        ({"reasoning_effort": "high"}, ("medium", "high")),
    ],
)
async def test_start_session_runs_on_the_model_the_user_asked_for(
    tmp_path: Path, asked: dict[str, JsonValue], sent: tuple[str, str | None]
) -> None:
    spawner = _FakeSpawner()

    started = await _executor(tmp_path, session_spawner=spawner)(
        _start_session("start", prompt="Fix it", placement="new_worktree", **asked)
    )

    assert isinstance(started, RustToolSucceededEvent)
    [request] = spawner.requests
    assert (request.model, request.reasoning_effort) == sent
    assert request.approval_policy == _PARENT_SETTINGS.approval_policy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asked", "reason"),
    [
        ({"model": "opus"}, "Pick one of: medium, claude-opus-5"),
        (
            {"model": "claude-opus-5", "reasoning_effort": "high"},
            "Pick one of: off, max",
        ),
        ({"reasoning_effort": "max"}, "Pick one of: low, high"),
        # The policy is always the parent's: a started session cannot widen it.
        ({"approval_policy": "auto-approve"}, ""),
    ],
)
async def test_start_session_refuses_a_setting_the_host_does_not_offer(
    tmp_path: Path, asked: dict[str, JsonValue], reason: str
) -> None:
    spawner = _FakeSpawner()

    rejected = await _executor(tmp_path, session_spawner=spawner)(
        _start_session("start", prompt="Fix it", placement="new_worktree", **asked)
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert reason in rejected.result.error.message
    assert spawner.requests == []


@pytest.mark.asyncio
async def test_start_session_without_a_spawner_is_a_correctable_tool_failure(
    tmp_path: Path,
) -> None:
    rejected = await _executor(tmp_path)(
        _start_session("start", prompt="Fix it", placement="current_checkout")
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert "not available" in rejected.result.error.message


@pytest.mark.asyncio
async def test_a_started_session_cannot_start_sessions_before_the_user_writes(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session another session started, not yet written to by the
    user.
    *Do*: Call start_session from it.
    *Assert*: The call fails back to the model without reaching the client, so
    started sessions cannot chain on their own.
    """
    spawner = _FakeSpawner()

    def handed_task(session_id: str) -> SessionSpawnOrigin:
        origin = _parent_in_a_managed_worktree(session_id)
        return SessionSpawnOrigin(
            checkout=origin.checkout, settings=origin.settings, awaits_user=True
        )

    rejected = await _executor(
        tmp_path, session_spawner=spawner, session_origin=handed_task
    )(_start_session("start", prompt="Fix it", placement="current_checkout"))

    assert isinstance(rejected, RustToolFailedEvent)
    assert "Do the task here" in rejected.result.error.message
    assert spawner.requests == []


@pytest.mark.asyncio
async def test_a_client_refusal_reaches_the_model(tmp_path: Path) -> None:
    async def refuse(_params: SessionSpawnParams) -> SessionSpawnResponse:
        raise SessionSpawnError("The project is not linked on this computer")

    execute = _executor(tmp_path, session_spawner=_FakeSpawner(refuse))
    rejected = await execute(
        _start_session(
            "start", prompt="Fix it", title="Fix", placement="current_checkout"
        )
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert "not linked on this computer" in rejected.result.error.message


@pytest.mark.asyncio
async def test_an_invalid_placement_fails_without_asking_the_client(
    tmp_path: Path,
) -> None:
    spawner = _FakeSpawner()
    execute = _executor(tmp_path, session_spawner=spawner)

    rejected = await execute(
        _start_session(
            "start", prompt="Fix it", title="Fix", placement="somewhere_else"
        )
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert spawner.requests == []


@pytest.mark.asyncio
async def test_a_spawn_the_client_never_answers_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vibe.app_server._session_spawn.SPAWN_TIMEOUT_SECONDS", 0.01)

    async def never(_params: SessionSpawnParams) -> SessionSpawnResponse:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    execute = _executor(tmp_path, session_spawner=_FakeSpawner(never))
    rejected = await execute(
        _start_session("start", prompt="Fix it", title="Fix", placement="new_worktree")
    )

    assert isinstance(rejected, RustToolFailedEvent)
    assert "may or may not have started" in rejected.result.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permission", "decision"), [(None, "ask"), ("always", "allow"), ("never", "deny")]
)
async def test_start_session_asks_for_approval_by_default(
    tmp_path: Path, permission: str | None, decision: str
) -> None:
    config = build_test_vibe_config(
        tools={}
        if permission is None
        else {START_SESSION_TOOL_NAME: {"permission": permission}}
    )
    orchestrator = FakeConfigOrchestrator(config)
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=tmp_path,
        harness_files=HarnessFilesManager().for_session(tmp_path),
        permission_getter=store.get_tool_permission,
    )
    resolver = UnifiedPermissionResolver(manager, store, orchestrator)
    resolver.provided_names.register(VIBE_TOOL_GROUP, VIBE_PROVIDED_TOOL_NAMES)

    assert VIBE_PROVIDED_TOOL_MODES[START_SESSION_TOOL_NAME] == "ask"
    outcome = await resolver.resolve(
        f"{VIBE_TOOL_GROUP}.{START_SESSION_TOOL_NAME}",
        {"prompt": "Fix it", "placement": "new_worktree"},
    )
    assert outcome.decision == decision


@pytest.mark.asyncio
async def test_start_session_names_the_session_by_its_title_and_link(
    tmp_path: Path,
) -> None:
    async def linked(params: SessionSpawnParams) -> SessionSpawnResponse:
        # The client may not manage to set the title; the requested one stands.
        return SessionSpawnResponse(session_id="spawned-1", url="/code/p/spawned-1")

    execute = _executor(tmp_path, session_spawner=_FakeSpawner(linked))
    started = await execute(
        _start_session(
            "start", prompt="Review #64565", title="Review", placement="new_worktree"
        )
    )

    assert isinstance(started, RustToolSucceededEvent)
    assert started.result.structured_content == {
        "session_id": "spawned-1",
        "title": "Review",
        "url": "/code/p/spawned-1",
        "message": "Started [Review](/code/p/spawned-1)",
    }


@pytest.mark.asyncio
async def test_start_session_names_the_session_by_the_clients_id(
    tmp_path: Path,
) -> None:
    """The id the client's session tools take, which the app server's own id
    stops matching after a handoff.
    """

    async def hosted(params: SessionSpawnParams) -> SessionSpawnResponse:
        return SessionSpawnResponse(
            session_id="app-server-1", client_session_id="code-session-1"
        )

    execute = _executor(tmp_path, session_spawner=_FakeSpawner(hosted))
    started = await execute(
        _start_session("start", prompt="Fix it", placement="new_worktree")
    )

    assert isinstance(started, RustToolSucceededEvent)
    assert isinstance(started.result.structured_content, dict)
    assert started.result.structured_content["session_id"] == "code-session-1"


@pytest.mark.asyncio
async def test_a_title_with_brackets_keeps_the_link_whole(tmp_path: Path) -> None:
    async def linked(_params: SessionSpawnParams) -> SessionSpawnResponse:
        return SessionSpawnResponse(session_id="spawned-1", url="/code/p/spawned-1")

    execute = _executor(tmp_path, session_spawner=_FakeSpawner(linked))
    started = await execute(
        _start_session(
            "start", prompt="Fix it", title="Fix [auth] retry", placement="new_worktree"
        )
    )

    assert isinstance(started, RustToolSucceededEvent)
    assert started.result.structured_content == {
        "session_id": "spawned-1",
        "title": "Fix [auth] retry",
        "url": "/code/p/spawned-1",
        "message": r"Started [Fix \[auth\] retry](/code/p/spawned-1)",
    }


@pytest.mark.asyncio
async def test_an_unnamed_session_is_left_to_name_itself(tmp_path: Path) -> None:
    async def untitled(_params: SessionSpawnParams) -> SessionSpawnResponse:
        return SessionSpawnResponse(session_id="spawned-1", url="/code/p/spawned-1")

    spawner = _FakeSpawner(untitled)
    execute = _executor(tmp_path, session_spawner=spawner)

    started = await execute(
        _start_session("start", prompt="Fix it", placement="new_worktree")
    )

    assert isinstance(started, RustToolSucceededEvent)
    assert spawner.requests[0].title is None
    assert started.result.structured_content == {
        "session_id": "spawned-1",
        "title": None,
        "url": "/code/p/spawned-1",
        "message": "Started [the new session](/code/p/spawned-1)",
    }


@pytest.mark.asyncio
async def test_running_the_same_call_again_asks_for_the_same_session(
    tmp_path: Path,
) -> None:
    spawner = _FakeSpawner()
    execute = _executor(tmp_path, "parent-session", session_spawner=spawner)

    for action_id in ("start-1", "start-1", "start-2"):
        await execute(
            _start_session(action_id, prompt="Fix it", placement="new_worktree")
        )

    first, replayed, other = (request.idempotency_key for request in spawner.requests)
    assert first == replayed
    assert other != first
