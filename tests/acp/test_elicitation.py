from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

from acp import PROTOCOL_VERSION, RequestError
from acp.schema import (
    AcceptElicitationResponse,
    ClientCapabilities,
    DeclineElicitationResponse,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    ElicitationFormSessionMode,
    TextContentBlock,
)
import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import start_test_app_server
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.app_server.local import LocalHarnessOptions
from vibe.app_server.session import AppServerSession
from vibe.core.types import FunctionCall, ToolCall


def _ask_user_question_tool_call() -> ToolCall:
    return ToolCall(
        id="question-1",
        index=0,
        function=FunctionCall(
            name="ask_user_question",
            arguments=json.dumps({
                "questions": [
                    {
                        "question": "Ship it?",
                        "header": "Release",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }
                ]
            }),
        ),
    )


def _elicitation_capable_client() -> ClientCapabilities:
    return ClientCapabilities(
        elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities())
    )


def _agent(
    backend: FakeBackend, *, captured_options: dict[str, object] | None = None
) -> tuple[VibeAcpAgent, FakeClient]:
    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        if captured_options is not None:
            captured_options["disabled_tools"] = list(
                options.session_options.disabled_tools
            )
            captured_options["callback_kinds"] = list(
                options.client.capabilities.callback_kinds
            )
        loop = build_test_agent_loop(backend=backend, enable_streaming=True)
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
            client_tool_handler=options.client_tool_handler,
        )

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    return agent, client


@pytest.mark.asyncio
async def test_ask_user_question_round_trips_through_acp_elicitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With elicitation support, ask_user_question is returned to the model and
    its answers round-trip through ACP elicitation back into the turn.
    """
    captured_options: dict[str, object] = {}
    agent, client = _agent(
        FakeBackend([
            [mock_llm_chunk(content="", tool_calls=[_ask_user_question_tool_call()])],
            [mock_llm_chunk(content="Shipped.")],
        ]),
        captured_options=captured_options,
    )
    captured: dict[str, object] = {}

    async def fake_create_elicitation(
        message: str, mode: object, **kwargs: object
    ) -> AcceptElicitationResponse:
        captured["message"] = message
        captured["mode"] = mode
        return AcceptElicitationResponse(action="accept", content={"q0": "Yes"})

    monkeypatch.setattr(client, "create_elicitation", fake_create_elicitation)

    try:
        await agent.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=_elicitation_capable_client(),
        )
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        # The interactive tools must not be blocked when the client can answer them.
        assert captured_options["disabled_tools"] == []
        assert captured_options["callback_kinds"] == ["approval", "user_input"]
        response = await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Should I ship?")],
        )

        assert response.stop_reason == "end_turn"

        # The tool was returned to the model (it called it) and the bridge
        # forwarded the question as an ACP form elicitation bound to the session.
        mode = captured["mode"]
        assert isinstance(mode, ElicitationFormSessionMode)
        assert mode.session_id == created.session_id
        schema = mode.requested_schema.model_dump(mode="json", by_alias=True)
        q0 = schema["properties"]["q0"]
        assert q0["type"] == "string"
        assert [option["const"] for option in q0["oneOf"]] == ["Yes", "No"]

        # The user's answer reached the model: the follow-up turn references it.
        messages = [
            update.content.text
            for notification in client._session_updates
            for update in [notification.update]
            if update.__class__.__name__ == "AgentMessageChunk"
        ]
        assert "Shipped." in messages
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_declined_elicitation_cancels_the_question_without_failing_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declined elicitation cancels the question; the turn still completes."""
    captured_options: dict[str, object] = {}
    agent, client = _agent(
        FakeBackend([
            [mock_llm_chunk(content="", tool_calls=[_ask_user_question_tool_call()])],
            [mock_llm_chunk(content="Never mind.")],
        ]),
        captured_options=captured_options,
    )
    monkeypatch.setattr(
        client,
        "create_elicitation",
        AsyncMock(return_value=DeclineElicitationResponse(action="decline")),
    )

    try:
        await agent.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=_elicitation_capable_client(),
        )
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        assert captured_options["disabled_tools"] == []
        response = await agent.prompt(
            session_id=created.session_id,
            prompt=[TextContentBlock(type="text", text="Should I ship?")],
        )

        assert response.stop_reason == "end_turn"
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_malformed_elicitation_response_fails_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accept whose content violates the schema is a client error: the turn
    fails and the error reaches the client, rather than silently cancelling.
    """
    agent, client = _agent(
        FakeBackend([
            [mock_llm_chunk(content="", tool_calls=[_ask_user_question_tool_call()])],
            [mock_llm_chunk(content="Shipped.")],
        ])
    )
    monkeypatch.setattr(
        client,
        "create_elicitation",
        AsyncMock(
            return_value=AcceptElicitationResponse(action="accept", content={"q0": 123})
        ),
    )

    try:
        await agent.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=_elicitation_capable_client(),
        )
        created = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        with pytest.raises(RequestError, match="Elicitation response for q0"):
            await agent.prompt(
                session_id=created.session_id,
                prompt=[TextContentBlock(type="text", text="Should I ship?")],
            )
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_tools_are_gated_by_elicitation_capability() -> None:
    """Without elicitation support the interactive tools are disabled and the
    client advertises only approval callbacks; with it, neither holds.
    """
    captured_disabled: dict[str, object] = {}
    agent_without, _client = _agent(
        FakeBackend([[mock_llm_chunk(content="ok")]]),
        captured_options=captured_disabled,
    )
    try:
        await agent_without.initialize(
            protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities()
        )
        await agent_without.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        assert captured_disabled["disabled_tools"] == [
            "ask_user_question",
            "exit_plan_mode",
        ]
        assert captured_disabled["callback_kinds"] == ["approval"]
    finally:
        await agent_without.close()

    captured_enabled: dict[str, object] = {}
    agent_with, _client = _agent(
        FakeBackend([[mock_llm_chunk(content="ok")]]), captured_options=captured_enabled
    )
    try:
        await agent_with.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=_elicitation_capable_client(),
        )
        await agent_with.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        assert captured_enabled["disabled_tools"] == []
        assert captured_enabled["callback_kinds"] == ["approval", "user_input"]
    finally:
        await agent_with.close()
