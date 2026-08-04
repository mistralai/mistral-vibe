from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock

from acp import RequestPermissionResponse
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    ContentToolCallContent,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.commands.registry import AcpCommandContext
from vibe.acp.commands.teleport import TELEPORT_PUSH_OPTION_ID
from vibe.acp.exceptions import COMPACTION_FAILED, CompactionError
from vibe.app_server.models import (
    PublicError,
    TeleportCheckingGit,
    TeleportComplete,
    TeleportFailed,
    TeleportPushing,
    TeleportPushRequired,
    TeleportStartingWorkflow,
    TeleportSummarizingContext,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)


def _texts(agent: VibeAcpAgent) -> list[str]:
    client = agent.client
    assert isinstance(client, FakeClient)
    return [
        update.update.content.text
        for update in client._session_updates
        if isinstance(update.update, AgentMessageChunk)
    ]


def _tool_text(update: ToolCallStart | ToolCallProgress) -> str | None:
    if not update.content:
        return None
    content = update.content[0]
    assert isinstance(content, ContentToolCallContent)
    assert isinstance(content.content, TextContentBlock)
    return content.content.text


@pytest.mark.asyncio
async def test_help_command_uses_the_acp_adapter_without_starting_a_turn(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/help")],
    )

    assert response.stop_reason == "end_turn"
    assert any(
        "/compact" in text and "/reload" in text for text in _texts(acp_agent_loop)
    )


@pytest.mark.asyncio
async def test_builtin_command_preserves_the_client_message_id(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    client = acp_agent_loop.client
    assert isinstance(client, FakeClient)

    await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/data-retention")],
    )

    user_messages = [
        update.update
        for update in client._session_updates
        if isinstance(update.update, UserMessageChunk)
    ]
    assert len(user_messages) == 1
    assert user_messages[0].message_id


@pytest.mark.asyncio
async def test_builtin_command_records_main_compatible_telemetry(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    record = Mock()
    monkeypatch.setattr(
        acp_agent_loop.sessions[created.session_id].app_server.resources.telemetry,
        "record",
        record,
    )

    await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/help")],
    )

    record.assert_called_once_with(
        "vibe.slash_command_used", {"command": "help", "command_type": "builtin"}
    )


@pytest.mark.asyncio
async def test_compact_command_maps_app_server_failure(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="Hello")],
    )
    session = acp_agent_loop.sessions[created.session_id]

    async def fail_compact(_instructions: str = "") -> str:
        raise AppServerResponseError(
            ProtocolError(
                code=ProtocolErrorCode.COMPACTION_FAILED,
                message="Compaction failed",
                data={"reason": "tool_call"},
            )
        )

    monkeypatch.setattr(session.app_server, "compact", fail_compact)

    with pytest.raises(CompactionError) as exc_info:
        await acp_agent_loop.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="/compact")],
        )

    assert exc_info.value.code == COMPACTION_FAILED
    assert exc_info.value.data == {"reason": "tool_call"}
    client = acp_agent_loop.client
    assert isinstance(client, FakeClient)
    failed = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, ToolCallProgress)
    ][-1]
    assert failed.status == "failed"
    assert failed.title == "Compaction failed"
    assert failed.raw_output == "Compaction failed"


@pytest.mark.asyncio
async def test_teleport_failure_completes_the_acp_tool_call(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    session = acp_agent_loop.sessions[created.session_id]
    session.commands.refresh(AcpCommandContext(vibe_code_enabled=True))
    vibe_code = session.app_server.resources.vibe_code
    monkeypatch.setattr(
        vibe_code, "open_projects", AsyncMock(return_value=(None, "project-id"))
    )

    async def failed_teleport(_prompt: str | None, *, project_id: str):
        assert project_id == "project-id"
        yield TeleportFailed(
            operation_id="teleport-1",
            error=PublicError(message="Teleport failed", code="teleport_failed"),
        )

    monkeypatch.setattr(vibe_code, "teleport", failed_teleport)
    client = acp_agent_loop.client
    assert isinstance(client, FakeClient)
    client._session_updates.clear()

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/teleport")],
    )

    assert response.stop_reason == "end_turn"
    assert response.field_meta == {
        "tool_name": "teleport",
        "teleport": {"status": "failed"},
    }
    failed = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, ToolCallProgress)
    ][-1]
    assert failed.status == "failed"
    assert failed.title == "Teleport failed"
    assert failed.raw_output == "Teleport failed"
    assert _tool_text(failed) == "Teleport failed"
    assert failed.field_meta == response.field_meta


@pytest.mark.asyncio
async def test_teleport_projects_every_stage_and_uses_push_specific_permission(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    session = acp_agent_loop.sessions[created.session_id]
    session.commands.refresh(AcpCommandContext(vibe_code_enabled=True))
    vibe_code = session.app_server.resources.vibe_code
    monkeypatch.setattr(
        vibe_code, "open_projects", AsyncMock(return_value=(None, "project-id"))
    )

    async def teleport(_prompt: str | None, *, project_id: str):
        assert project_id == "project-id"
        yield TeleportSummarizingContext(operation_id="teleport-1")
        yield TeleportCheckingGit(operation_id="teleport-1")
        yield TeleportPushRequired(
            operation_id="teleport-1", unpushed_count=2, branch_not_pushed=True
        )
        yield TeleportPushing(operation_id="teleport-1")
        yield TeleportStartingWorkflow(operation_id="teleport-1")
        yield TeleportComplete(
            operation_id="teleport-1", url="https://example.test/session"
        )

    monkeypatch.setattr(vibe_code, "teleport", teleport)
    respond_to_push = AsyncMock()
    monkeypatch.setattr(vibe_code, "respond_to_push", respond_to_push)
    client = acp_agent_loop.client
    assert isinstance(client, FakeClient)
    request_permission = AsyncMock(
        return_value=RequestPermissionResponse(
            outcome=AllowedOutcome(
                outcome="selected", option_id=TELEPORT_PUSH_OPTION_ID
            )
        )
    )
    monkeypatch.setattr(client, "request_permission", request_permission)
    client._session_updates.clear()

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/teleport")],
    )

    updates = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, ToolCallStart | ToolCallProgress)
    ]
    assert [update.title for update in updates] == [
        "Teleporting session to Vibe Code Web...",
        "Summarizing context...",
        "Preparing workspace...",
        "Push required",
        "Syncing with remote...",
        "Starting Vibe Code Web session...",
        "Teleported to Vibe Code Web",
    ]
    assert [_tool_text(update) for update in updates] == [
        "Preparing workspace...",
        "Summarizing context...",
        "Preparing workspace...",
        "Your branch doesn't exist on remote. Push to continue?",
        "Syncing with remote...",
        "Starting Vibe Code Web session...",
        "Teleported to Vibe Code Web: https://example.test/session",
    ]
    permission_call = request_permission.await_args
    assert permission_call is not None
    assert permission_call.kwargs["tool_call"].title == (
        "Your branch doesn't exist on remote. Push to continue?"
    )
    assert [option.name for option in permission_call.kwargs["options"]] == [
        "Push and continue",
        "Cancel",
    ]
    assert [option.option_id for option in permission_call.kwargs["options"]] == [
        "teleport_push_and_continue",
        "teleport_cancel",
    ]
    respond_to_push.assert_awaited_once_with("teleport-1", approved=True)
    assert response.field_meta == {
        "tool_name": "teleport",
        "teleport": {"status": "completed", "url": "https://example.test/session"},
    }


@pytest.mark.asyncio
async def test_teleport_precondition_error_is_a_terminal_command_reply(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    session = acp_agent_loop.sessions[created.session_id]
    session.commands.refresh(AcpCommandContext(vibe_code_enabled=True))
    monkeypatch.setattr(
        session.app_server.resources.vibe_code,
        "open_projects",
        AsyncMock(
            side_effect=AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS,
                    message="No conversation history to teleport.",
                )
            )
        ),
    )

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/teleport")],
    )

    assert response.stop_reason == "end_turn"
    assert response.field_meta == {
        "tool_name": "teleport",
        "teleport": {"status": "no_history"},
    }
    assert _texts(acp_agent_loop)[-1] == "No conversation history to teleport."


@pytest.mark.asyncio
async def test_teleport_start_error_completes_the_acp_tool_call(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    session = acp_agent_loop.sessions[created.session_id]
    session.commands.refresh(AcpCommandContext(vibe_code_enabled=True))
    vibe_code = session.app_server.resources.vibe_code
    monkeypatch.setattr(
        vibe_code, "open_projects", AsyncMock(return_value=(None, "project-id"))
    )

    async def fail_teleport(_prompt: str | None, *, project_id: str):
        assert project_id == "project-id"
        if False:
            yield
        raise AppServerResponseError(
            ProtocolError(
                code=ProtocolErrorCode.CONFLICT, message="Another operation is active"
            )
        )

    monkeypatch.setattr(vibe_code, "teleport", fail_teleport)
    client = acp_agent_loop.client
    assert isinstance(client, FakeClient)
    client._session_updates.clear()

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/teleport")],
    )

    failed = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, ToolCallProgress)
    ][-1]
    assert response.field_meta == {
        "tool_name": "teleport",
        "teleport": {"status": "failed"},
    }
    assert failed.status == "failed"
    assert failed.raw_output == "Another operation is active"
    assert _tool_text(failed) == "Another operation is active"


@pytest.mark.asyncio
async def test_reload_failure_is_reported_as_a_command_reply(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    monkeypatch.setattr(
        acp_agent_loop.sessions[created.session_id].app_server.resources.config,
        "reload",
        AsyncMock(side_effect=RuntimeError("invalid config")),
    )

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/reload")],
    )

    assert response.stop_reason == "end_turn"
    assert _texts(acp_agent_loop)[-1] == "Failed to reload config: invalid config"


@pytest.mark.asyncio
async def test_mcp_status_rejects_extra_arguments_like_main(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[TextContentBlock(type="text", text="/mcp status extra")],
    )

    assert response.stop_reason == "end_turn"
    assert _texts(acp_agent_loop)[-1] == "Usage: `/mcp status`"


@pytest.mark.asyncio
async def test_proxy_setup_normalizes_key_and_matches_main_success_message(
    acp_agent_loop: VibeAcpAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    config = acp_agent_loop.sessions[created.session_id].app_server.resources.config
    update_proxy = AsyncMock()
    monkeypatch.setattr(config, "update_proxy", update_proxy)

    response = await acp_agent_loop.prompt(
        session_id=created.session_id,
        prompt=[
            TextContentBlock(
                type="text", text="/proxy-setup https_proxy https://proxy.test"
            )
        ],
    )

    assert response.stop_reason == "end_turn"
    update_proxy.assert_awaited_once_with({"HTTPS_PROXY": "https://proxy.test"})
    assert _texts(acp_agent_loop)[-1] == (
        "Set `HTTPS_PROXY=https://proxy.test` in ~/.vibe/.env\n\n"
        "Please start a new chat for changes to take effect."
    )
