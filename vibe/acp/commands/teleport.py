from __future__ import annotations

from typing import Literal, assert_never

from acp.schema import (
    ContentToolCallContent,
    FileEditToolCallContent,
    PermissionOption,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallStatus,
    ToolCallUpdate,
)

from vibe.app_server.models import (
    TeleportCheckingGit,
    TeleportComplete,
    TeleportEvent,
    TeleportFailed,
    TeleportPushing,
    TeleportPushRequired,
    TeleportStartingWorkflow,
    TeleportSummarizingContext,
)

TELEPORT_PUSH_OPTION_ID = "teleport_push_and_continue"
TELEPORT_CANCEL_OPTION_ID = "teleport_cancel"

type TeleportAcpStatus = Literal[
    "starting",
    "summarizing_context",
    "preparing_workspace",
    "push_required",
    "syncing_remote",
    "starting_workflow",
    "completed",
    "failed",
    "no_history",
    "unavailable",
]


def teleport_field_meta(
    status: TeleportAcpStatus,
    *,
    url: str | None = None,
    unpushed_count: int | None = None,
    branch_not_pushed: bool | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {"status": status}
    if url is not None:
        value["url"] = url
    if unpushed_count is not None:
        value["unpushedCount"] = unpushed_count
    if branch_not_pushed is not None:
        value["branchNotPushed"] = branch_not_pushed
    return {"tool_name": "teleport", "teleport": value}


def teleport_start_update(tool_call_id: str) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=tool_call_id,
        title="Teleporting session to Vibe Code Web...",
        kind="other",
        status="in_progress",
        content=_content("Preparing workspace..."),
        field_meta=teleport_field_meta("starting"),
    )


def teleport_event_update(tool_call_id: str, event: TeleportEvent) -> ToolCallProgress:
    match event:
        case TeleportSummarizingContext():
            update = _progress(
                tool_call_id,
                title="Summarizing context...",
                text="Summarizing context...",
                teleport_status="summarizing_context",
            )
        case TeleportCheckingGit():
            update = _progress(
                tool_call_id,
                title="Preparing workspace...",
                text="Preparing workspace...",
                teleport_status="preparing_workspace",
            )
        case TeleportPushRequired(
            unpushed_count=count, branch_not_pushed=branch_not_pushed
        ):
            update = _progress(
                tool_call_id,
                title="Push required",
                text=teleport_push_question(event),
                teleport_status="push_required",
                unpushed_count=count,
                branch_not_pushed=branch_not_pushed,
            )
        case TeleportPushing():
            update = _progress(
                tool_call_id,
                title="Syncing with remote...",
                text="Syncing with remote...",
                teleport_status="syncing_remote",
            )
        case TeleportStartingWorkflow():
            update = _progress(
                tool_call_id,
                title="Starting Vibe Code Web session...",
                text="Starting Vibe Code Web session...",
                teleport_status="starting_workflow",
            )
        case TeleportComplete(url=url):
            update = _progress(
                tool_call_id,
                title="Teleported to Vibe Code Web",
                status="completed",
                text=f"Teleported to Vibe Code Web: {url}",
                raw_output=url,
                url=url,
                teleport_status="completed",
            )
        case TeleportFailed(error=error):
            update = teleport_failed_update(tool_call_id, error.message)
        case _ as unreachable:
            assert_never(unreachable)
    return update


def teleport_failed_update(tool_call_id: str, message: str) -> ToolCallProgress:
    return _progress(
        tool_call_id,
        title="Teleport failed",
        status="failed",
        text=message,
        raw_output=message,
        teleport_status="failed",
    )


def teleport_push_question(event: TeleportPushRequired) -> str:
    if event.branch_not_pushed:
        return "Your branch doesn't exist on remote. Push to continue?"
    suffix = "s" if event.unpushed_count != 1 else ""
    return f"You have {event.unpushed_count} unpushed commit{suffix}. Push to continue?"


def teleport_push_request(
    tool_call_id: str, event: TeleportPushRequired
) -> ToolCallUpdate:
    return ToolCallUpdate(
        tool_call_id=tool_call_id,
        title=teleport_push_question(event),
        kind="execute",
        status="pending",
        field_meta=teleport_field_meta(
            "push_required",
            unpushed_count=event.unpushed_count,
            branch_not_pushed=event.branch_not_pushed,
        ),
    )


def teleport_push_options() -> list[PermissionOption]:
    return [
        PermissionOption(
            option_id=TELEPORT_PUSH_OPTION_ID,
            name="Push and continue",
            kind="allow_once",
        ),
        PermissionOption(
            option_id=TELEPORT_CANCEL_OPTION_ID, name="Cancel", kind="reject_once"
        ),
    ]


def _progress(
    tool_call_id: str,
    *,
    title: str,
    teleport_status: TeleportAcpStatus,
    status: ToolCallStatus = "in_progress",
    text: str | None = None,
    raw_output: str | None = None,
    url: str | None = None,
    unpushed_count: int | None = None,
    branch_not_pushed: bool | None = None,
) -> ToolCallProgress:
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=tool_call_id,
        title=title,
        kind="other",
        status=status,
        content=_content(text) if text is not None else None,
        raw_output=raw_output,
        field_meta=teleport_field_meta(
            teleport_status,
            url=url,
            unpushed_count=unpushed_count,
            branch_not_pushed=branch_not_pushed,
        ),
    )


def _content(
    text: str,
) -> list[ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent]:
    return [
        ContentToolCallContent(
            type="content", content=TextContentBlock(type="text", text=text)
        )
    ]
