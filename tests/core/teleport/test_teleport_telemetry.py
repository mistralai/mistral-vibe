from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop, TeleportError
from vibe.core.telemetry.types import LaunchContext
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.teleport import TeleportService
from vibe.core.teleport.types import (
    TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH,
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportMessageContext,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
    TeleportSummarizingContextEvent,
)
from vibe.core.types import ImageAttachment, InlineImageSource, LLMMessage, Role


def _set_teleport_service(agent_loop: AgentLoop, service: object) -> None:
    agent_loop._teleport_service = cast(TeleportService, service)


class TestTeleportAgentLoopTelemetry:
    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_summarizes_context(
        self, agent_loop: AgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        class FakeTeleportService:
            message_context: TeleportMessageContext | None = None

            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **kwargs: object
            ) -> AsyncGenerator[object, object]:
                self.message_context = cast(
                    TeleportMessageContext | None, kwargs.get("message_context")
                )
                yield TeleportCheckingGitEvent()
                yield TeleportStartingWorkflowEvent()
                yield TeleportCompleteEvent(url="https://chat.example.com/123")

        service = FakeTeleportService()
        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        agent_loop.messages.append(LLMMessage(role=Role.assistant, content="done"))
        agent_loop._summarize_teleport_context = AsyncMock(
            return_value="Prior CLI context"
        )
        _set_teleport_service(agent_loop, service)

        events = [event async for event in agent_loop.teleport_to_vibe_code(None)]

        assert isinstance(events[0], TeleportSummarizingContextEvent)
        assert service.message_context is not None
        assert service.message_context.summary == "Prior CLI context"
        assert service.message_context.source is not None
        assert service.message_context.source.entrypoint == "unknown"
        assert telemetry_events[-1]["event_name"] == "vibe.teleport_completed"
        assert {
            "push_required": False,
            "nb_session_messages": 2,
            "context_summary": "generated",
            "context_summary_chars": 17,
            "session_id": agent_loop.session_id,
        }.items() <= telemetry_events[-1]["properties"].items()

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_propagates_client_from_launch_context(
        self,
    ) -> None:
        class FakeTeleportService:
            message_context: TeleportMessageContext | None = None

            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **kwargs: object
            ) -> AsyncGenerator[object, object]:
                self.message_context = cast(
                    TeleportMessageContext | None, kwargs.get("message_context")
                )
                yield TeleportCheckingGitEvent()
                yield TeleportStartingWorkflowEvent()
                yield TeleportCompleteEvent(url="https://chat.example.com/123")

        agent_loop = build_test_agent_loop(
            launch_context=LaunchContext(
                agent_entrypoint="acp",
                agent_version="1.0.0",
                client_name="mistral-vibe-vscode",
                client_version="2.19.1",
            )
        )
        service = FakeTeleportService()
        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        agent_loop.messages.append(LLMMessage(role=Role.assistant, content="done"))
        agent_loop._summarize_teleport_context = AsyncMock(
            return_value="Prior ACP context"
        )
        _set_teleport_service(agent_loop, service)

        _ = [event async for event in agent_loop.teleport_to_vibe_code(None)]

        assert service.message_context is not None
        assert service.message_context.source is not None
        assert service.message_context.source.entrypoint == "acp"
        assert service.message_context.source.client_name == "mistral-vibe-vscode"

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_summarizes_image_only_context(
        self, agent_loop: AgentLoop
    ) -> None:
        class FakeTeleportService:
            message_context: TeleportMessageContext | None = None

            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **kwargs: object
            ) -> AsyncGenerator[object, object]:
                self.message_context = cast(
                    TeleportMessageContext | None, kwargs.get("message_context")
                )
                yield TeleportCheckingGitEvent()
                yield TeleportStartingWorkflowEvent()
                yield TeleportCompleteEvent(url="https://chat.example.com/123")

        service = FakeTeleportService()
        image = ImageAttachment(
            source=InlineImageSource(data="aW1hZ2U="),
            alias="screenshot.png",
            mime_type="image/png",
        )
        agent_loop.messages.append(
            LLMMessage(role=Role.user, content="", images=[image])
        )
        agent_loop.messages.append(LLMMessage(role=Role.assistant, content="done"))
        agent_loop.messages.append(LLMMessage(role=Role.user, content="continue"))
        agent_loop._summarize_teleport_context = AsyncMock(
            return_value="Prior image context"
        )
        _set_teleport_service(agent_loop, service)

        events = [event async for event in agent_loop.teleport_to_vibe_code(None)]

        assert isinstance(events[0], TeleportSummarizingContextEvent)
        assert service.message_context is not None
        assert service.message_context.summary == "Prior image context"

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_continues_without_overlong_summary(
        self, agent_loop: AgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        class FakeTeleportService:
            message_context: TeleportMessageContext | None = None

            async def __aenter__(self) -> FakeTeleportService:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def execute(
                self, *_args: object, **kwargs: object
            ) -> AsyncGenerator[object, object]:
                self.message_context = cast(
                    TeleportMessageContext | None, kwargs.get("message_context")
                )
                yield TeleportCheckingGitEvent()
                yield TeleportStartingWorkflowEvent()
                yield TeleportCompleteEvent(url="https://chat.example.com/123")

        service = FakeTeleportService()
        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        agent_loop.messages.append(LLMMessage(role=Role.assistant, content="done"))
        agent_loop._summarize_teleport_context = AsyncMock(
            return_value="x" * (TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH + 1)
        )
        _set_teleport_service(agent_loop, service)

        events = [event async for event in agent_loop.teleport_to_vibe_code(None)]

        assert isinstance(events[0], TeleportSummarizingContextEvent)
        assert isinstance(events[-1], TeleportCompleteEvent)
        assert service.message_context is None
        assert telemetry_events[-1]["event_name"] == "vibe.teleport_completed"
        assert {
            "context_summary": "failed",
            "context_summary_chars": None,
            "session_id": agent_loop.session_id,
        }.items() <= telemetry_events[-1]["properties"].items()

    @pytest.mark.asyncio
    async def test_summarize_teleport_context_disables_tools(self) -> None:
        backend = FakeBackend(mock_llm_chunk(content="Prior context"))
        agent_loop = build_test_agent_loop(backend=backend)
        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))

        summary = await agent_loop._summarize_teleport_context(
            prompt="ship it", resolved_prompt="ship it"
        )

        assert summary == "Prior context"
        assert backend.requests_tools == [[]]
        assert backend.requests_tool_choices == [None]

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
        assert {
            "push_required": True,
            "nb_session_messages": 1,
            "context_summary": "skipped",
            "context_summary_chars": None,
            "session_id": agent_loop.session_id,
        }.items() <= telemetry_events[-1]["properties"].items()

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_sends_project_picker_context_on_success(
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
                yield TeleportCompleteEvent(url="https://chat.example.com/123")

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(
            None,
            project_picker={
                "project_picker_shown": True,
                "project_selection_source": "selected_existing",
                "project_candidate_count_loaded": 3,
                "project_multi_repo_match_count": 1,
                "saved_project_link_cleared": False,
                "project_repo_remote_changed": False,
            },
        )
        async for _ in gen:
            pass

        assert telemetry_events[-1]["event_name"] == "vibe.teleport_completed"
        assert {
            "project_picker_shown": True,
            "project_selection_source": "selected_existing",
            "project_candidate_count_loaded": 3,
            "project_multi_repo_match_count": 1,
            "saved_project_link_cleared": False,
            "project_repo_remote_changed": False,
        }.items() <= telemetry_events[-1]["properties"].items()

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
                raise ServiceTeleportError(
                    "Workflow api-key-123 could not be started.",
                    telemetry_details={"http_status_code": 502},
                )

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(None)
        with pytest.raises(TeleportError, match="api-key-123"):
            async for _ in gen:
                pass

        assert telemetry_events[-1]["event_name"] == "vibe.teleport_failed"
        assert {
            "stage": "workflow_start",
            "error_class": "ServiceTeleportError",
            "push_required": False,
            "nb_session_messages": 1,
            "context_summary": "skipped",
            "context_summary_chars": None,
            "http_status_code": 502,
            "session_id": agent_loop.session_id,
        }.items() <= telemetry_events[-1]["properties"].items()
        assert "api-key-123" not in str(telemetry_events[-1]["properties"])

    @pytest.mark.asyncio
    async def test_teleport_to_vibe_code_marks_stale_saved_link_cleared(
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
                raise ServiceTeleportError(
                    "Project not found", telemetry_details={"http_status_code": 404}
                )
                if False:
                    yield None

        agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))
        _set_teleport_service(agent_loop, FakeTeleportService())

        gen = agent_loop.teleport_to_vibe_code(
            None,
            project_picker={
                "project_picker_shown": False,
                "project_selection_source": "saved_link",
                "project_candidate_count_loaded": 0,
                "project_multi_repo_match_count": 0,
                "saved_project_link_cleared": False,
                "project_repo_remote_changed": False,
            },
        )
        with pytest.raises(TeleportError, match="Project not found"):
            async for _ in gen:
                pass

        assert telemetry_events[-1]["event_name"] == "vibe.teleport_failed"
        assert {
            "project_selection_source": "saved_link",
            "saved_project_link_cleared": True,
            "http_status_code": 404,
        }.items() <= telemetry_events[-1]["properties"].items()

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
        assert {
            "stage": "cancelled",
            "error_class": "ServiceTeleportError",
            "push_required": True,
            "nb_session_messages": 1,
            "context_summary": "skipped",
            "context_summary_chars": None,
            "session_id": agent_loop.session_id,
        }.items() <= telemetry_events[-1]["properties"].items()

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
        assert {
            "stage": "cancelled",
            "error_class": "CancelledError",
            "push_required": False,
            "nb_session_messages": 1,
            "context_summary": "skipped",
            "context_summary_chars": None,
            "session_id": agent_loop.session_id,
        }.items() <= telemetry_events[-1]["properties"].items()

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
        assert {
            "stage": "cancelled",
            "error_class": "CancelledError",
            "push_required": False,
            "nb_session_messages": 1,
            "context_summary": "skipped",
            "context_summary_chars": None,
            "session_id": agent_loop.session_id,
        }.items() <= telemetry_events[-1]["properties"].items()
