from __future__ import annotations

from contextlib import aclosing
from typing import Literal, cast
from uuid import uuid4

from acp import Client, PromptResponse
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    ContentToolCallContent,
    PermissionOption,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallStatus,
    ToolCallUpdate,
)

from vibe.acp.session import AcpSessionLoop
from vibe.core.agent_loop import TeleportError
from vibe.core.teleport.telemetry import send_teleport_early_failure_telemetry
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
)
from vibe.core.types import Role

TELEPORT_PUSH_OPTION_ID = "teleport_push_and_continue"
TELEPORT_CANCEL_OPTION_ID = "teleport_cancel"
TELEPORT_FIELD_META_KEY = "teleport"
type TeleportAcpStatus = Literal[
    "starting",
    "preparing_workspace",
    "push_required",
    "syncing_remote",
    "starting_workflow",
    "completed",
    "failed",
    "no_history",
    "unavailable",
]


def _teleport_field_meta(
    status: TeleportAcpStatus,
    *,
    url: str | None = None,
    unpushed_count: int | None = None,
    branch_not_pushed: bool | None = None,
) -> dict[str, object]:
    teleport_meta: dict[str, object] = {"status": status}
    if url is not None:
        teleport_meta["url"] = url
    if unpushed_count is not None:
        teleport_meta["unpushedCount"] = unpushed_count
    if branch_not_pushed is not None:
        teleport_meta["branchNotPushed"] = branch_not_pushed
    return {"tool_name": "teleport", TELEPORT_FIELD_META_KEY: teleport_meta}


def _teleport_progress_update(
    tool_call_id: str,
    *,
    title: str,
    status: ToolCallStatus = "in_progress",
    text: str | None = None,
    raw_output: str | None = None,
    url: str | None = None,
    teleport_status: TeleportAcpStatus,
    unpushed_count: int | None = None,
    branch_not_pushed: bool | None = None,
) -> ToolCallProgress:
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=tool_call_id,
        title=title,
        kind="other",
        status=status,
        raw_output=raw_output,
        content=(
            [
                ContentToolCallContent(
                    type="content", content=TextContentBlock(type="text", text=text)
                )
            ]
            if text
            else None
        ),
        field_meta=_teleport_field_meta(
            teleport_status,
            url=url,
            unpushed_count=unpushed_count,
            branch_not_pushed=branch_not_pushed,
        ),
    )


async def _teleport_command_reply(
    client: Client,
    session: AcpSessionLoop,
    text: str,
    message_id: str,
    *,
    field_meta: dict[str, object],
) -> PromptResponse:
    await client.session_update(
        session_id=session.id,
        update=AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text=text),
            message_id=str(uuid4()),
            field_meta=field_meta,
        ),
    )
    return PromptResponse(
        stop_reason="end_turn", user_message_id=message_id, field_meta=field_meta
    )


async def _request_teleport_push_approval(
    client: Client,
    session: AcpSessionLoop,
    tool_call_id: str,
    *,
    count: int,
    branch_not_pushed: bool,
) -> TeleportPushResponseEvent:
    if branch_not_pushed:
        question = "Your branch doesn't exist on remote. Push to continue?"
    else:
        word = f"commit{'s' if count != 1 else ''}"
        question = f"You have {count} unpushed {word}. Push to continue?"

    await client.session_update(
        session_id=session.id,
        update=_teleport_progress_update(
            tool_call_id,
            title="Push required",
            text=question,
            teleport_status="push_required",
            unpushed_count=count,
            branch_not_pushed=branch_not_pushed,
        ),
    )

    response = await client.request_permission(
        session_id=session.id,
        tool_call=ToolCallUpdate(
            tool_call_id=tool_call_id,
            title=question,
            kind="execute",
            status="pending",
            field_meta=_teleport_field_meta(
                "push_required",
                unpushed_count=count,
                branch_not_pushed=branch_not_pushed,
            ),
        ),
        options=[
            PermissionOption(
                option_id=TELEPORT_PUSH_OPTION_ID,
                name="Push and continue",
                kind="allow_once",
            ),
            PermissionOption(
                option_id=TELEPORT_CANCEL_OPTION_ID, name="Cancel", kind="reject_once"
            ),
        ],
    )

    if response.outcome.outcome != "selected":
        return TeleportPushResponseEvent(approved=False)

    outcome = cast(AllowedOutcome, response.outcome)
    return TeleportPushResponseEvent(
        approved=outcome.option_id == TELEPORT_PUSH_OPTION_ID
    )


async def handle_teleport_command(
    client: Client, session: AcpSessionLoop, message_id: str
) -> PromptResponse:
    if not session.agent_loop.base_config.vibe_code_enabled:
        return await _teleport_command_reply(
            client,
            session,
            "Teleport is not available because Vibe Code is disabled.",
            message_id,
            field_meta=_teleport_field_meta("unavailable"),
        )

    if not session.agent_loop.config.is_active_model_mistral():
        send_teleport_early_failure_telemetry(
            session.agent_loop.telemetry_client,
            stage="ineligible",
            error_class="TeleportIneligibleError",
            nb_session_messages=len(session.agent_loop.messages[1:]),
        )
        return await _teleport_command_reply(
            client,
            session,
            "Teleport requires an active Mistral model. Switch to a Mistral "
            "model, then try again.",
            message_id,
            field_meta=_teleport_field_meta("unavailable"),
        )

    last_user_message = next(
        (
            msg
            for msg in reversed(session.agent_loop.messages)
            if msg.role == Role.user and not msg.injected
        ),
        None,
    )
    has_resolvable_prompt = (
        last_user_message is not None
        and isinstance(last_user_message.content, str)
        and bool(last_user_message.content)
    )
    if not has_resolvable_prompt:
        send_teleport_early_failure_telemetry(
            session.agent_loop.telemetry_client,
            stage="no_history",
            error_class="TeleportNoHistoryError",
            nb_session_messages=len(session.agent_loop.messages[1:]),
        )
        return await _teleport_command_reply(
            client,
            session,
            "No conversation history to teleport.",
            message_id,
            field_meta=_teleport_field_meta("no_history"),
        )

    tool_call_id = str(uuid4())
    await client.session_update(
        session_id=session.id,
        update=ToolCallStart(
            session_update="tool_call",
            tool_call_id=tool_call_id,
            title="Teleporting session to Vibe Code Web...",
            kind="other",
            status="in_progress",
            content=[
                ContentToolCallContent(
                    type="content",
                    content=TextContentBlock(
                        type="text", text="Preparing workspace..."
                    ),
                )
            ],
            field_meta=_teleport_field_meta("starting"),
        ),
    )

    final_url: str | None = None
    try:
        async with aclosing(session.agent_loop.teleport_to_vibe_code(None)) as events:
            response: TeleportPushResponseEvent | None = None
            while True:
                try:
                    event = await events.asend(response)
                except StopAsyncIteration:
                    break
                response = None

                match event:
                    case TeleportCheckingGitEvent():
                        await client.session_update(
                            session_id=session.id,
                            update=_teleport_progress_update(
                                tool_call_id,
                                title="Preparing workspace...",
                                text="Preparing workspace...",
                                teleport_status="preparing_workspace",
                            ),
                        )
                    case TeleportPushRequiredEvent(
                        unpushed_count=count, branch_not_pushed=branch_not_pushed
                    ):
                        response = await _request_teleport_push_approval(
                            client,
                            session,
                            tool_call_id,
                            count=count,
                            branch_not_pushed=branch_not_pushed,
                        )
                    case TeleportPushingEvent():
                        await client.session_update(
                            session_id=session.id,
                            update=_teleport_progress_update(
                                tool_call_id,
                                title="Syncing with remote...",
                                text="Syncing with remote...",
                                teleport_status="syncing_remote",
                            ),
                        )
                    case TeleportStartingWorkflowEvent():
                        await client.session_update(
                            session_id=session.id,
                            update=_teleport_progress_update(
                                tool_call_id,
                                title="Starting Vibe Code Web session...",
                                text="Starting Vibe Code Web session...",
                                teleport_status="starting_workflow",
                            ),
                        )
                    case TeleportCompleteEvent(url=url):
                        final_url = url
                        await client.session_update(
                            session_id=session.id,
                            update=_teleport_progress_update(
                                tool_call_id,
                                title="Teleported to Vibe Code Web",
                                status="completed",
                                text=f"Teleported to Vibe Code Web: {url}",
                                raw_output=url,
                                url=url,
                                teleport_status="completed",
                            ),
                        )
    except TeleportError as e:
        await client.session_update(
            session_id=session.id,
            update=_teleport_progress_update(
                tool_call_id,
                title="Teleport failed",
                status="failed",
                text=str(e),
                raw_output=str(e),
                teleport_status="failed",
            ),
        )
        return PromptResponse(
            stop_reason="end_turn",
            user_message_id=message_id,
            field_meta=_teleport_field_meta("failed"),
        )

    return PromptResponse(
        stop_reason="end_turn",
        user_message_id=message_id,
        field_meta=_teleport_field_meta("completed", url=final_url),
    )
