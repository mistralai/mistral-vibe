"""Run Vibe's own tools inside a Unified Harness session.

Todo semantics come from the legacy tool's own module so the two harnesses cannot
drift; only the storage differs, and the list is deliberately not carried across
compaction -- that is the scratchpad's job.

Design: ``vibe/docs/design/unified-harness-todo-and-scratchpad.md``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Container, Mapping, Sequence
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
    RustProtocolError,
    RustProvidedToolCall,
    RustProvidedToolCallAction,
    RustProvidedToolDefinition,
    RustTextContentBlock,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolGroupDefinition,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    # Deferred, unlike `protocol` above: this subpackage pulls in `mcp` and
    # `mistralai`, which `test_importing_app_server_local_does_not_import_mcp_package`
    # keeps off the CLI's startup path.
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ProvidedToolExecutor,
        ProvidedToolExecutorFactory,
        ToolApprovalMode,
    )

from vibe.app_server._unified_scratchpad import (
    SCRATCHPAD_TOOL_DESCRIPTION,
    SCRATCHPAD_TOOL_NAME,
    ScratchpadArgs,
    ScratchpadResult,
    run_scratchpad,
    scratchpad_dir,
)
from vibe.core.tools.builtins.todo import (
    Todo,
    TodoArgs,
    TodoConfig,
    TodoItem,
    TodoResult,
    read_todos,
    write_todos,
)
from vibe.core.utils.matching import name_matches

VIBE_TOOL_GROUP = "vibe"

TODO_TOOL_NAME = "todo"

# Routes the Runtime gates by, mapped to the names Vibe configures the tools under,
# so the permission resolver reads `[tools.todo]` for `vibe.todo`.
VIBE_PROVIDED_TOOL_NAMES = {f"{VIBE_TOOL_GROUP}.{TODO_TOOL_NAME}": TODO_TOOL_NAME}

# Per tool, because the two do not share an approval bar. Todo sits at "ask" so the
# permission resolver -- not the smart-approve classifier -- answers it, which is
# what makes `[tools.todo] permission` mean the same thing on both harnesses. The
# scratchpad writes only inside the session's own storage and has no legacy
# counterpart to read a permission from, so it is pinned allowed.
VIBE_PROVIDED_TOOL_MODES: dict[str, ToolApprovalMode] = {
    TODO_TOOL_NAME: "ask",
    SCRATCHPAD_TOOL_NAME: "allow",
}


def vibe_tool_groups(
    available_tools: Container[str],
    *,
    enabled_tools: Sequence[str] = (),
    disabled_tools: Sequence[str] = (),
) -> list[RustToolGroupDefinition]:
    # Todo follows the legacy roster, so a tool switched off in config stays off here
    # too. The scratchpad has no catalogue entry to be filtered out of, so it reads
    # the same globs itself rather than escaping tool filtering entirely.
    tools: list[RustProvidedToolDefinition] = []
    if TODO_TOOL_NAME in available_tools:
        tools.append(
            RustProvidedToolDefinition(
                name=TODO_TOOL_NAME,
                description=Todo.get_full_description(),
                input_schema=TodoArgs.model_json_schema(),
                output_schema=TodoResult.model_json_schema(),
                # Programmatic too: a `run_typescript` program is one tool call, so a
                # direct-only tool leaves the list frozen until the program returns.
                exposure="direct_and_programmatic",
            )
        )
    if _passes_tool_filters(
        SCRATCHPAD_TOOL_NAME, enabled=enabled_tools, disabled=disabled_tools
    ):
        tools.append(
            RustProvidedToolDefinition(
                name=SCRATCHPAD_TOOL_NAME,
                description=SCRATCHPAD_TOOL_DESCRIPTION,
                input_schema=ScratchpadArgs.model_json_schema(),
                output_schema=ScratchpadResult.model_json_schema(),
                exposure="direct",
            )
        )
    if not tools:
        return []

    return [
        RustToolGroupDefinition(
            name=VIBE_TOOL_GROUP, description="Vibe's own tools.", tools=tools
        )
    ]


def _passes_tool_filters(
    name: str, *, enabled: Sequence[str], disabled: Sequence[str]
) -> bool:
    if enabled and not name_matches(name, list(enabled)):
        return False
    return not (disabled and name_matches(name, list(disabled)))


class VibeProvidedTools:
    def __init__(self) -> None:
        # Held here rather than in the executor closure so a rewind can drop it: an
        # in-place rewind keeps the session alive, and a list that outlived the
        # truncated history would let `todo read` return the discarded entries.
        self._todos: dict[str, list[TodoItem]] = {}

    def executor_factory(
        self, storage_root: str | Path, *, max_todos: Callable[[], int]
    ) -> ProvidedToolExecutorFactory:
        root = Path(storage_root).expanduser().resolve()

        def build(session_id: str) -> ProvidedToolExecutor:
            todos = self._todos.setdefault(session_id, [])
            directory = scratchpad_dir(root, session_id)

            async def execute(
                action: RustProvidedToolCallAction,
            ) -> RustToolSucceededEvent | RustToolFailedEvent:
                try:
                    result = await _run(
                        action.call,
                        todos=todos,
                        directory=directory,
                        max_todos=max_todos(),
                    )
                # A rejected call reaches the model as a correctable tool failure
                # rather than killing the turn.
                except (ValidationError, ValueError, OSError) as error:
                    return _failed(action, error)
                return _succeeded(action, result)

            return execute

        return build

    def forget_todos(self, session_id: str) -> None:
        # In place, because the session's executor closure holds this same list.
        self._todos.get(session_id, []).clear()

    def adopt(self, other: VibeProvidedTools) -> None:
        # By reference: a live session's executor closes over the lists the previous
        # holder handed out, so a copy would leave them unreachable.
        self._todos = other._todos


async def _run(
    call: RustProvidedToolCall,
    *,
    todos: list[TodoItem],
    directory: Path,
    max_todos: int,
) -> BaseModel:
    if call.tool_name == TODO_TOOL_NAME:
        return _run_todo(call.arguments, todos, max_todos=max_todos)
    if call.tool_name == SCRATCHPAD_TOOL_NAME:
        args = ScratchpadArgs.model_validate(call.arguments)
        return await asyncio.to_thread(run_scratchpad, args, directory=directory)
    raise ValueError(f"Unsupported {VIBE_TOOL_GROUP} tool: {call.tool_name}")


def _run_todo(
    arguments: object, todos: list[TodoItem], *, max_todos: int
) -> TodoResult:
    args = TodoArgs.model_validate(arguments)
    if args.action == "read":
        return read_todos(todos)

    result = write_todos(args.todos or [], max_todos=max_todos)
    todos[:] = result.todos
    return result


def resolved_max_todos(overrides: Mapping[str, Any] | None) -> int:
    """``[tools.todo].max_todos``, validated by the legacy tool's own config model."""
    try:
        return TodoConfig.model_validate(dict(overrides or {})).max_todos
    except ValidationError:
        return TodoConfig().max_todos


def _succeeded(
    action: RustProvidedToolCallAction, result: BaseModel
) -> RustToolSucceededEvent:
    payload = result.model_dump(mode="json")
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult(
            content=[RustTextContentBlock(text=json.dumps(payload))],
            structured_content=payload,
        ),
    )


def _failed(
    action: RustProvidedToolCallAction, error: Exception
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code="tool_failed", message=str(error), retryable=False, details=None
            )
        ),
    )


__all__ = [
    "TODO_TOOL_NAME",
    "VIBE_PROVIDED_TOOL_MODES",
    "VIBE_PROVIDED_TOOL_NAMES",
    "VIBE_TOOL_GROUP",
    "VibeProvidedTools",
    "resolved_max_todos",
    "vibe_tool_groups",
]
