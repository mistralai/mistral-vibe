from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from acp.schema import CancelElicitationResponse
import pytest

from vibe.acp.agent import VibeAcpAgent
from vibe.acp.exceptions import InternalError
from vibe.acp.session import AcpSession
from vibe.app_server.events import AppServerEvent, CallbackRequested
from vibe.app_server.models import (
    OpenCallbackState,
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicTurn,
    PublicTurnStatus,
    UserInputCallbackDetail,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.session import AppServerSession
from vibe.questions import QuestionChoice, UserQuestion, UserQuestionRequest


def _make_agent(*, connection_closed: bool) -> tuple[VibeAcpAgent, AcpSession]:
    agent = VibeAcpAgent()
    client = MagicMock()
    connection = MagicMock()
    connection._closed = connection_closed
    connection._disconnected = connection_closed
    client._conn = connection
    agent.client = cast(Any, client)

    app_server = MagicMock(spec=AppServerSession)
    app_server.act = MagicMock(side_effect=asyncio.CancelledError)
    session = AcpSession(
        session_id="test-session",
        app_server=app_server,
        cwd=Path.cwd(),
        commands=MagicMock(),
    )
    agent.sessions[session.id] = session
    command_controller = cast(Any, agent)._command_controller
    command_controller.execute = AsyncMock(return_value=None)
    cast(Any, agent)._record_skill_command = MagicMock()
    cast(Any, agent)._prepare_turn_input = AsyncMock(return_value=MagicMock())
    cast(Any, agent)._send_usage_update = MagicMock()
    cast(Any, agent)._usage = MagicMock(return_value=None)
    return agent, session


class TestPromptCancel:
    @pytest.mark.asyncio
    async def test_connection_teardown_reraises_without_usage_update(self) -> None:
        agent, session = _make_agent(connection_closed=True)

        with pytest.raises(asyncio.CancelledError):
            await agent.prompt(session.id, [])

        send_usage_update = cast(Any, agent)._send_usage_update
        send_usage_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_level_cancel_returns_cancelled_and_sends_usage(self) -> None:
        agent, session = _make_agent(connection_closed=False)

        response = await agent.prompt(session.id, [])

        assert response.stop_reason == "cancelled"
        send_usage_update = cast(Any, agent)._send_usage_update
        send_usage_update.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_answer_to_a_callback_the_interrupt_closed_returns_cancelled(
        self,
    ) -> None:
        agent, session = _make_agent(connection_closed=False)
        _stub_callback_answer_failing_with(
            agent, session, ProtocolErrorCode.CALLBACK_CLOSED
        )

        response = await agent.prompt(session.id, [])

        assert response.stop_reason == "cancelled"

    @pytest.mark.asyncio
    async def test_other_callback_answer_failures_still_fail_the_prompt(self) -> None:
        agent, session = _make_agent(connection_closed=False)
        _stub_callback_answer_failing_with(
            agent, session, ProtocolErrorCode.INTERNAL_ERROR
        )

        with pytest.raises(InternalError):
            await agent.prompt(session.id, [])


def _stub_callback_answer_failing_with(
    agent: VibeAcpAgent, session: AcpSession, answer_error: ProtocolErrorCode
) -> None:
    callback = PublicCallbackEntry(
        id="callback:question-1",
        session_id=session.id,
        turn_id="turn-1",
        callback_id="question-1",
        title="Question",
        generation_status=PublicEntryGenerationStatus.IN_PROGRESS,
        created_at=1,
        updated_at=1,
        detail=UserInputCallbackDetail(
            request=UserQuestionRequest(
                questions=[
                    UserQuestion(
                        question="Continue?",
                        options=[
                            QuestionChoice(label="Yes"),
                            QuestionChoice(label="No"),
                        ],
                    )
                ]
            )
        ),
        state=OpenCallbackState(),
    )

    async def events(*_args: Any, **_kwargs: Any) -> AsyncIterator[AppServerEvent]:
        yield CallbackRequested(callback)

    app_server = cast(Any, session.app_server)
    app_server.act = MagicMock(side_effect=events)
    app_server.deny_callback = AsyncMock(
        side_effect=AppServerResponseError(
            ProtocolError(code=answer_error, message="Callback is closed: question-1")
        )
    )
    app_server.state.turns = [
        PublicTurn(
            id="turn-1",
            session_id=session.id,
            status=PublicTurnStatus.INTERRUPTED,
            started_at=1,
            completed_at=2,
        )
    ]
    cast(Any, agent.client).create_elicitation = AsyncMock(
        return_value=CancelElicitationResponse(action="cancel")
    )
