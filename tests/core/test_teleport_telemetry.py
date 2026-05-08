from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from vibe.core.agent_loop import AgentLoop, TeleportError
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.teleport import TeleportService
from vibe.core.teleport.types import (
    TeleportAuthRequiredEvent,
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportFetchingUrlEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
    TeleportWaitingForGitHubEvent,
)
from vibe.core.types import LLMMessage, Role


def _set_teleport_service(agent_loop: AgentLoop, service: object) -> None:
    agent_loop._teleport_service = cast(TeleportService, service)


class TestTeleportAgentLoopTelemetry:
    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_sends_completed_success(
        self, agent_loop: AgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        class FakeTeleportService:
            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                yield TeleportCheckingGitEvent()
                yield TeleportPushRequiredEvent()
                yield TeleportPushingEvent()
                yield TeleportStartingWorkflowEvent()
                yield TeleportWaitingForGitHubEvent()
                yield TeleportAuthRequiredEvent(oauth_url="https://github.com/auth")
                yield TeleportFetchingUrlEvent()
                yield TeleportCompleteEvent(url="https://chat.example.com/123")

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(None)
        response = None
        events = []
        while True:
            try:
                event = await gen.asend(response)
            except StopAsyncIteration:
                break
            events.append(event)
            response = (
                TeleportPushResponseEvent(approved=True)
                if isinstance(event, TeleportPushRequiredEvent)
                else None
            )

        assert isinstance(events[-1], TeleportCompleteEvent)
        assert telemetry_events[-1]["event_name"] == "vibe.teleport_completed"
        assert telemetry_events[-1]["properties"] == {
            "push_required": True,
            "github_auth_required": True,
            "nb_session_messages": 1,
            "session_id": agent_loop.session_id,
        }

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_sends_failed_stage(
        self, agent_loop: AgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        class FakeTeleportService:
            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                yield TeleportCheckingGitEvent()
                yield TeleportStartingWorkflowEvent()
                raise ServiceTeleportError("Workflow api-key-123 could not be started.")

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(None)
        with pytest.raises(TeleportError, match="api-key-123"):
            async for _ in gen:
                pass

        assert telemetry_events[-1]["event_name"] == "vibe.teleport_failed"
        assert telemetry_events[-1]["properties"] == {
            "stage": "workflow_start",
            "error_class": "ServiceTeleportError",
            "push_required": False,
            "github_auth_required": False,
            "nb_session_messages": 1,
            "session_id": agent_loop.session_id,
        }
        assert "api-key-123" not in str(telemetry_events[-1]["properties"])

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_sends_failed_cancelled(
        self, agent_loop: AgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        class FakeTeleportService:
            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                yield TeleportCheckingGitEvent()
                response = yield TeleportPushRequiredEvent()
                if (
                    not isinstance(response, TeleportPushResponseEvent)
                    or not response.approved
                ):
                    raise ServiceTeleportError(
                        "Teleport cancelled: changes not pushed."
                    )

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(None)
        assert isinstance(await gen.asend(None), TeleportCheckingGitEvent)
        assert isinstance(await gen.asend(None), TeleportPushRequiredEvent)

        with pytest.raises(TeleportError, match="Teleport cancelled"):
            await gen.asend(TeleportPushResponseEvent(approved=False))

        assert telemetry_events[-1]["event_name"] == "vibe.teleport_failed"
        assert telemetry_events[-1]["properties"] == {
            "stage": "cancelled",
            "error_class": "ServiceTeleportError",
            "push_required": True,
            "github_auth_required": False,
            "nb_session_messages": 1,
            "session_id": agent_loop.session_id,
        }

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_sends_failed_when_task_cancelled(
        self, agent_loop: AgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        class FakeTeleportService:
            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                yield TeleportCheckingGitEvent()
                raise asyncio.CancelledError

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(None)
        assert isinstance(await gen.asend(None), TeleportCheckingGitEvent)

        with pytest.raises(asyncio.CancelledError):
            await gen.asend(None)

        assert telemetry_events[-1]["event_name"] == "vibe.teleport_failed"
        assert telemetry_events[-1]["properties"] == {
            "stage": "cancelled",
            "error_class": "CancelledError",
            "push_required": False,
            "github_auth_required": False,
            "nb_session_messages": 1,
            "session_id": agent_loop.session_id,
        }

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_sends_failed_when_consumer_closes_generator(
        self, agent_loop: AgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        class FakeTeleportService:
            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **_kwargs: object
            ) -> AsyncGenerator[object, object]:
                yield TeleportCheckingGitEvent()
                yield TeleportPushRequiredEvent()

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(None)
        assert isinstance(await gen.asend(None), TeleportCheckingGitEvent)

        await gen.aclose()

        assert telemetry_events[-1]["event_name"] == "vibe.teleport_failed"
        assert telemetry_events[-1]["properties"] == {
            "stage": "cancelled",
            "error_class": "CancelledError",
            "push_required": False,
            "github_auth_required": False,
            "nb_session_messages": 1,
            "session_id": agent_loop.session_id,
        }
