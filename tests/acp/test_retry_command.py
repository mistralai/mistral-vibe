from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from acp.schema import AgentMessageChunk
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.commands.controller import AcpCommandController, InjectedPrompt
from vibe.acp.commands.registry import AcpCommandRegistry
from vibe.acp.session import AcpSession
from vibe.app_server.models import (
    PublicError,
    PublicTurn,
    PublicTurnStatus,
    TurnErrorCode,
)
from vibe.app_server.session import AppServerSession
from vibe.utils.retry_prompt import build_retry_prompt


def _turn(status: PublicTurnStatus, code: TurnErrorCode | None = None) -> PublicTurn:
    return PublicTurn(
        id="turn-1",
        session_id="session",
        status=status,
        started_at=1,
        completed_at=2,
        error=PublicError(message="failed", code=code) if code else None,
    )


def _controller_and_session(
    latest_turn: PublicTurn | None,
) -> tuple[AcpCommandController, AcpSession, FakeClient]:
    client = FakeClient()

    async def send_config_options(_session: AcpSession) -> None:
        return None

    controller = AcpCommandController(lambda: client, send_config_options)
    app_server = MagicMock(spec=AppServerSession)
    app_server.resources = MagicMock()
    app_server.state.latest_turn = latest_turn
    session = AcpSession(
        session_id="session",
        app_server=cast(AppServerSession, app_server),
        cwd=Path.cwd(),
        commands=AcpCommandRegistry(),
    )
    return controller, session, client


def _agent_messages(client: FakeClient) -> list[str]:
    return [
        notification.update.content.text
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
        and hasattr(notification.update.content, "text")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn",
    [
        _turn(PublicTurnStatus.INTERRUPTED),
        _turn(PublicTurnStatus.FAILED, TurnErrorCode.BACKEND_ERROR),
        _turn(PublicTurnStatus.FAILED, TurnErrorCode.INCOMPLETE_STREAM),
    ],
)
async def test_retry_reruns_an_interrupted_or_retryable_failed_turn(
    turn: PublicTurn,
) -> None:
    controller, session, _client = _controller_and_session(turn)

    result = await controller.execute(session, "/retry keep it short", "message-1")

    assert result == InjectedPrompt(build_retry_prompt("keep it short"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn",
    [
        None,
        _turn(PublicTurnStatus.COMPLETED),
        _turn(PublicTurnStatus.FAILED, TurnErrorCode.INVALID_API_KEY),
        _turn(PublicTurnStatus.FAILED),
    ],
)
async def test_retry_with_nothing_to_retry_says_so_and_runs_no_turn(
    turn: PublicTurn | None,
) -> None:
    controller, session, client = _controller_and_session(turn)

    result = await controller.execute(session, "/retry", "message-1")

    assert not isinstance(result, InjectedPrompt)
    assert _agent_messages(client) == ["No failed or interrupted response to retry."]
