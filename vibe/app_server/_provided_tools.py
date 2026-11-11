"""Run Vibe's own tools inside a Unified Harness session.

Todo semantics come from the legacy tool's own module so the two harnesses cannot
drift; only the storage differs, and the list is deliberately not carried across
compaction -- durable notes belong in the scratchpad, which the agent reads back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Container, Mapping, Sequence
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

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

if TYPE_CHECKING:
    # Deferred, unlike `protocol` above: this subpackage pulls in `mcp` and
    # `mistralai`, which `test_importing_app_server_local_does_not_import_mcp_package`
    # keeps off the CLI's startup path.
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ProvidedToolExecutor,
        ProvidedToolExecutorFactory,
        ToolApprovalMode,
    )

from vibe.app_server._cron import (
    CRON_TOOL_DESCRIPTION,
    CRON_TOOL_NAME,
    CronArgs,
    CronResult,
    cron_input_schema,
    run_cron,
)
from vibe.app_server._enter_worktree import (
    ENTER_WORKTREE_TOOL_DESCRIPTION,
    ENTER_WORKTREE_TOOL_NAME,
    EnterWorktreeArgs,
    EnterWorktreeError,
    EnterWorktreeResult,
    WorktreeEnterer,
    run_enter_worktree,
)
from vibe.app_server._session_peers import (
    ARCHIVE_SESSIONS_TOOL_DESCRIPTION,
    ARCHIVE_SESSIONS_TOOL_NAME,
    LIST_SESSIONS_TOOL_DESCRIPTION,
    LIST_SESSIONS_TOOL_NAME,
    MARK_SESSIONS_SEEN_TOOL_DESCRIPTION,
    MARK_SESSIONS_SEEN_TOOL_NAME,
    READ_SESSION_TOOL_DESCRIPTION,
    READ_SESSION_TOOL_NAME,
    RESTORE_SESSIONS_TOOL_DESCRIPTION,
    RESTORE_SESSIONS_TOOL_NAME,
    SEND_TO_SESSION_TOOL_DESCRIPTION,
    SEND_TO_SESSION_TOOL_NAME,
    ArchiveSessionsArgs,
    HopsOf,
    ListSessionsArgs,
    MarkSessionsSeenArgs,
    PeerContext,
    ReadSessionArgs,
    RestoreSessionsArgs,
    SenderOf,
    SendToSessionArgs,
    SessionPeerError,
    SessionPeers,
    StarterOf,
    TurnStarts,
    run_archive_sessions,
    run_list_sessions,
    run_mark_sessions_seen,
    run_read_session,
    run_restore_sessions,
    run_send_to_session,
)
from vibe.app_server._session_spawn import (
    START_SESSION_TOOL_DESCRIPTION,
    START_SESSION_TOOL_NAME,
    SessionOriginResolver,
    SessionSpawner,
    SessionSpawnError,
    SpawnModel,
    StartSessionArgs,
    StartSessionResult,
    run_start_session,
    start_session_input_schema,
)
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    UnifiedScheduledLoops,
)
from vibe.app_server._unified_scratchpad import (
    SCRATCHPAD_TOOL_DESCRIPTION,
    SCRATCHPAD_TOOL_NAME,
    ScratchpadArgs,
    ScratchpadResult,
    run_scratchpad,
    scratchpad_dir,
)
from vibe.app_server.protocol import (
    SessionPeerArchiveResponse,
    SessionPeerListResponse,
    SessionPeerMarkSeenResponse,
    SessionPeerReadResponse,
    SessionPeerRestoreResponse,
    SessionPeerSendResponse,
)
from vibe.core.loop import LoopError
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
VIBE_PROVIDED_TOOL_NAMES = {
    f"{VIBE_TOOL_GROUP}.{TODO_TOOL_NAME}": TODO_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{CRON_TOOL_NAME}": CRON_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{START_SESSION_TOOL_NAME}": START_SESSION_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{LIST_SESSIONS_TOOL_NAME}": LIST_SESSIONS_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{READ_SESSION_TOOL_NAME}": READ_SESSION_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{SEND_TO_SESSION_TOOL_NAME}": SEND_TO_SESSION_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{ARCHIVE_SESSIONS_TOOL_NAME}": ARCHIVE_SESSIONS_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{RESTORE_SESSIONS_TOOL_NAME}": RESTORE_SESSIONS_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{MARK_SESSIONS_SEEN_TOOL_NAME}": MARK_SESSIONS_SEEN_TOOL_NAME,
    f"{VIBE_TOOL_GROUP}.{ENTER_WORKTREE_TOOL_NAME}": ENTER_WORKTREE_TOOL_NAME,
}

# "ask" routes through Vibe's permission resolver. Cron defaults to allowed there,
# while explicit config and session permission overrides still take precedence.
# Starting, messaging, archiving or restoring sessions acts on real work, so they
# keep the resolver's default: ask. Listing, reading and marking sessions seen
# default to allowed there.
# Scratchpad writes only inside the session's own storage and is pinned allowed.
VIBE_PROVIDED_TOOL_MODES: dict[str, ToolApprovalMode] = {
    TODO_TOOL_NAME: "ask",
    CRON_TOOL_NAME: "ask",
    START_SESSION_TOOL_NAME: "ask",
    LIST_SESSIONS_TOOL_NAME: "ask",
    READ_SESSION_TOOL_NAME: "ask",
    SEND_TO_SESSION_TOOL_NAME: "ask",
    ARCHIVE_SESSIONS_TOOL_NAME: "ask",
    RESTORE_SESSIONS_TOOL_NAME: "ask",
    MARK_SESSIONS_SEEN_TOOL_NAME: "ask",
    SCRATCHPAD_TOOL_NAME: "allow",
    # Moving off the user's checkout is the safe direction: never asked about.
    ENTER_WORKTREE_TOOL_NAME: "allow",
}


def vibe_tool_groups(
    available_tools: Container[str],
    *,
    enabled_tools: Sequence[str] = (),
    disabled_tools: Sequence[str] = (),
    session_spawn: Sequence[SpawnModel] | None = None,
    session_peers: bool = False,
    session_peer_restore: bool = False,
    session_peer_mark_seen: bool = False,
    enter_worktree: bool = False,
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
    if _passes_tool_filters(
        CRON_TOOL_NAME, enabled=enabled_tools, disabled=disabled_tools
    ):
        tools.append(
            RustProvidedToolDefinition(
                name=CRON_TOOL_NAME,
                description=CRON_TOOL_DESCRIPTION,
                input_schema=cron_input_schema(),
                output_schema=CronResult.model_json_schema(),
                exposure="direct",
            )
        )
    if session_spawn is not None and _passes_tool_filters(
        START_SESSION_TOOL_NAME, enabled=enabled_tools, disabled=disabled_tools
    ):
        tools.append(
            RustProvidedToolDefinition(
                name=START_SESSION_TOOL_NAME,
                description=START_SESSION_TOOL_DESCRIPTION,
                input_schema=start_session_input_schema(session_spawn),
                output_schema=StartSessionResult.model_json_schema(),
                exposure="direct",
            )
        )
    if enter_worktree and enter_worktree_offered(
        enabled_tools=enabled_tools, disabled_tools=disabled_tools
    ):
        tools.append(
            RustProvidedToolDefinition(
                name=ENTER_WORKTREE_TOOL_NAME,
                description=ENTER_WORKTREE_TOOL_DESCRIPTION,
                input_schema=EnterWorktreeArgs.model_json_schema(),
                output_schema=EnterWorktreeResult.model_json_schema(),
                exposure="direct",
            )
        )
    if session_peers:
        for name, description, args, result in (
            (
                LIST_SESSIONS_TOOL_NAME,
                LIST_SESSIONS_TOOL_DESCRIPTION,
                ListSessionsArgs,
                SessionPeerListResponse,
            ),
            (
                READ_SESSION_TOOL_NAME,
                READ_SESSION_TOOL_DESCRIPTION,
                ReadSessionArgs,
                SessionPeerReadResponse,
            ),
            (
                SEND_TO_SESSION_TOOL_NAME,
                SEND_TO_SESSION_TOOL_DESCRIPTION,
                SendToSessionArgs,
                SessionPeerSendResponse,
            ),
            (
                ARCHIVE_SESSIONS_TOOL_NAME,
                ARCHIVE_SESSIONS_TOOL_DESCRIPTION,
                ArchiveSessionsArgs,
                SessionPeerArchiveResponse,
            ),
            (
                RESTORE_SESSIONS_TOOL_NAME,
                RESTORE_SESSIONS_TOOL_DESCRIPTION,
                RestoreSessionsArgs,
                SessionPeerRestoreResponse,
            ),
            (
                MARK_SESSIONS_SEEN_TOOL_NAME,
                MARK_SESSIONS_SEEN_TOOL_DESCRIPTION,
                MarkSessionsSeenArgs,
                SessionPeerMarkSeenResponse,
            ),
        ):
            if name == RESTORE_SESSIONS_TOOL_NAME and not session_peer_restore:
                continue
            if name == MARK_SESSIONS_SEEN_TOOL_NAME and not session_peer_mark_seen:
                continue
            if _passes_tool_filters(
                name, enabled=enabled_tools, disabled=disabled_tools
            ):
                tools.append(
                    RustProvidedToolDefinition(
                        name=name,
                        description=description,
                        input_schema=args.model_json_schema(),
                        output_schema=result.model_json_schema(by_alias=False),
                        # A program can filter sessions on any field; what acts
                        # on them stays a direct call, so each is asked about.
                        exposure="direct_and_programmatic"
                        if name in _PEER_READ_TOOL_NAMES
                        else "direct",
                    )
                )
    if not tools:
        return []

    return [
        RustToolGroupDefinition(
            name=VIBE_TOOL_GROUP, description="Vibe's own tools.", tools=tools
        )
    ]


def enter_worktree_offered(
    *, enabled_tools: Sequence[str], disabled_tools: Sequence[str]
) -> bool:
    return _passes_tool_filters(
        ENTER_WORKTREE_TOOL_NAME, enabled=enabled_tools, disabled=disabled_tools
    )


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

    def executor_factory(  # noqa: PLR0913 - one optional hook per tool family, all keyword-only
        self,
        storage_root: str | Path,
        *,
        max_todos: Callable[[], int],
        scheduled_loops: Callable[[str], Awaitable[UnifiedScheduledLoops]]
        | None = None,
        session_spawner: SessionSpawner | None = None,
        session_origin: SessionOriginResolver | None = None,
        session_peers: SessionPeers | None = None,
        hops_of: HopsOf | None = None,
        sender_of: SenderOf | None = None,
        starter_of: StarterOf | None = None,
        turn_starts: TurnStarts | None = None,
        enter_worktree: WorktreeEnterer | None = None,
    ) -> ProvidedToolExecutorFactory:
        root = Path(storage_root).expanduser().resolve()

        def build(session_id: str) -> ProvidedToolExecutor:
            todos = self._todos.setdefault(session_id, [])
            directory = scratchpad_dir(root, session_id)

            async def execute(
                action: RustProvidedToolCallAction,
            ) -> RustToolSucceededEvent | RustToolFailedEvent:
                try:
                    if action.call.tool_name == CRON_TOOL_NAME:
                        if scheduled_loops is None:
                            raise ValueError(
                                "Scheduled prompts are not available for this session"
                            )
                        result = await run_cron(
                            CronArgs.model_validate(action.call.arguments),
                            scheduled_loops=await scheduled_loops(session_id),
                        )
                    elif action.call.tool_name == START_SESSION_TOOL_NAME:
                        if session_spawner is None or session_origin is None:
                            raise ValueError(
                                "Starting sessions is not available for this session"
                            )
                        result = await run_start_session(
                            StartSessionArgs.model_validate(action.call.arguments),
                            parent_session_id=session_id,
                            invocation_id=action.action_id,
                            origin_of=session_origin,
                            spawner=session_spawner,
                        )
                    elif action.call.tool_name == ENTER_WORKTREE_TOOL_NAME:
                        if enter_worktree is None:
                            raise ValueError(
                                "Worktrees are not available for this session"
                            )
                        result = await run_enter_worktree(
                            EnterWorktreeArgs.model_validate(action.call.arguments),
                            session_id=session_id,
                            enter=enter_worktree,
                        )
                    elif peer_tool := _PEER_TOOLS.get(action.call.tool_name):
                        if session_peers is None or hops_of is None:
                            raise ValueError(
                                "Other sessions are not reachable from this session"
                            )
                        result = await peer_tool(
                            action,
                            session_id,
                            session_peers,
                            PeerContext(
                                hops_of=hops_of,
                                sender_of=sender_of,
                                starter_of=starter_of,
                                **(
                                    {}
                                    if turn_starts is None
                                    else {"turn_starts": turn_starts}
                                ),
                            ),
                        )
                    else:
                        result = await _run(
                            action.call,
                            todos=todos,
                            directory=directory,
                            max_todos=max_todos(),
                        )
                # A rejected call reaches the model as a correctable tool failure
                # rather than killing the turn.
                except (
                    ValidationError,
                    ValueError,
                    OSError,
                    LoopError,
                    ScheduledLoopStoreError,
                    SessionSpawnError,
                    SessionPeerError,
                    EnterWorktreeError,
                ) as error:
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


type _PeerToolRunner = Callable[
    [RustProvidedToolCallAction, str, SessionPeers, PeerContext], Awaitable[BaseModel]
]

_PEER_TOOLS: dict[str, _PeerToolRunner] = {
    LIST_SESSIONS_TOOL_NAME: lambda action, session_id, peers, _peer: run_list_sessions(
        ListSessionsArgs.model_validate(action.call.arguments),
        session_id=session_id,
        peers=peers,
    ),
    READ_SESSION_TOOL_NAME: lambda action, session_id, peers, _peer: run_read_session(
        ReadSessionArgs.model_validate(action.call.arguments),
        session_id=session_id,
        peers=peers,
    ),
    SEND_TO_SESSION_TOOL_NAME: lambda action, session_id, peers, peer: (
        run_send_to_session(
            SendToSessionArgs.model_validate(action.call.arguments),
            session_id=session_id,
            invocation_id=action.action_id,
            peers=peers,
            peer=peer,
        )
    ),
    ARCHIVE_SESSIONS_TOOL_NAME: lambda action, session_id, peers, _peer: (
        run_archive_sessions(
            ArchiveSessionsArgs.model_validate(action.call.arguments),
            session_id=session_id,
            peers=peers,
        )
    ),
    RESTORE_SESSIONS_TOOL_NAME: lambda action, session_id, peers, _peer: (
        run_restore_sessions(
            RestoreSessionsArgs.model_validate(action.call.arguments),
            session_id=session_id,
            peers=peers,
        )
    ),
    MARK_SESSIONS_SEEN_TOOL_NAME: lambda action, session_id, peers, _peer: (
        run_mark_sessions_seen(
            MarkSessionsSeenArgs.model_validate(action.call.arguments),
            session_id=session_id,
            peers=peers,
        )
    ),
}
_PEER_READ_TOOL_NAMES = frozenset({LIST_SESSIONS_TOOL_NAME, READ_SESSION_TOOL_NAME})


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
    # Snake case, like the arguments the model passes back.
    payload = result.model_dump(mode="json", by_alias=False)
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult(
            content=[
                RustTextContentBlock(text=json.dumps(payload, ensure_ascii=False))
            ],
            structured_content=payload,
        ),
    )


def _failed(
    action: RustProvidedToolCallAction, error: Exception
) -> RustToolFailedEvent:
    reason = (
        error.reason
        if isinstance(error, SessionSpawnError | SessionPeerError)
        else None
    )
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code="tool_failed",
                message=str(error),
                retryable=False,
                details=None if reason is None else {"reason": reason},
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
