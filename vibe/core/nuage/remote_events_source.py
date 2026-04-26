from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import TypeAdapter, ValidationError

from vibe.core.agent_loop import AgentLoopStateError
from vibe.core.config import VibeConfig
from vibe.core.nuage.agent_models import (
    _SUBMIT_INPUT_UPDATE_NAME,
    ChatInputModel,
    SubmitInputModel,
)
from vibe.core.nuage.client import WorkflowsClient
from vibe.core.nuage.events import WorkflowEvent
from vibe.core.nuage.exceptions import ErrorCode, WorkflowsException
from vibe.core.nuage.remote_workflow_event_translator import (
    PendingInputRequest,
    RemoteWorkflowEventTranslator,
)
from vibe.core.nuage.streaming import StreamEventsQueryParams
from vibe.core.nuage.workflow import WorkflowExecutionStatus
from vibe.core.tools.manager import ToolManager
from vibe.core.types import AgentStats, BaseEvent, LLMMessage, Role

_RETRYABLE_STREAM_ERRORS = ("peer closed connection", "incomplete chunked read")
_WORKFLOW_EVENT_ADAPTER = TypeAdapter(WorkflowEvent)


class RemoteEventsSource:
    def __init__(self, session_id: str, config: VibeConfig) -> None:
        self.session_id = session_id
        self._config = config
        self.messages: list[LLMMessage] = []
        self.stats = AgentStats()
        self._tool_manager = ToolManager(lambda: config)
        self._next_start_seq = 0
        self._client: WorkflowsClient | None = None
        self._translator = RemoteWorkflowEventTranslator(
            available_tools=self._tool_manager._available,
            stats=self.stats,
            merge_message=self._merge_message,
        )

    @property
    def is_waiting_for_input(self) -> bool:
        return self._translator.pending_input_request is not None

    @property
    def _pending_input_request(self) -> PendingInputRequest | None:
        return self._translator.pending_input_request

    @_pending_input_request.setter
    def _pending_input_request(self, value: PendingInputRequest | None) -> None:
        self._translator.pending_input_request = value

    @property
    def _task_state(self) -> dict[str, dict[str, Any]]:
        return self._translator.task_state

    @property
    def is_terminated(self) -> bool:
        return self._translator.last_status is not None

    @property
    def is_failed(self) -> bool:
        return self._translator.last_status == WorkflowExecutionStatus.FAILED

    @property
    def is_canceled(self) -> bool:
        return self._translator.last_status == WorkflowExecutionStatus.CANCELED

    @property
    def client(self) -> WorkflowsClient:
        if self._client is None:
            self._client = WorkflowsClient(
                base_url=self._config.nuage_base_url,
                api_key=self._config.nuage_api_key,
                timeout=self._config.api_timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def attach(self) -> AsyncGenerator[BaseEvent, None]:
        async for event in self._stream_remote_events(stop_on_idle_boundary=False):
            yield event
        for event in self._translator.flush_open_tool_calls():
            yield event

    async def send_prompt(self, msg: str) -> None:
        pending = self._translator.pending_input_request
        if pending is None:
            return

        if not self._is_chat_input_request(pending):
            raise AgentLoopStateError(
                "Remote workflow is waiting for structured input that this UI does not support."
            )

        await self.client.update_workflow(
            self.session_id,
            _SUBMIT_INPUT_UPDATE_NAME,
            SubmitInputModel(
                task_id=pending.task_id,
                input={"message": [{"type": "text", "text": msg}]},
            ),
        )
        self._translator.pending_input_request = None

    def _is_chat_input_request(self, request: PendingInputRequest) -> bool:
        title = request.input_schema.get("title")
        return title == ChatInputModel.model_config.get("title")

    async def _stream_remote_events(
        self, stop_on_idle_boundary: bool = True
    ) -> AsyncGenerator[BaseEvent]:
        retry_count = 0
        max_retry_count = 3
        done = False

        while not done:
            params = StreamEventsQueryParams(
                workflow_exec_id=self.session_id, start_seq=self._next_start_seq
            )
            stream = self.client.stream_events(params)
            try:
                async for payload in stream:
                    retry_count = 0
                    if payload.broker_sequence is not None:
                        self._next_start_seq = payload.broker_sequence + 1

                    event = self._normalize_stream_event(payload.data)
                    if event is None:
                        continue
                    for emitted_event in self._consume_workflow_event(event):
                        yield emitted_event

                    if self.is_terminated:
                        done = True
                        break

                    if stop_on_idle_boundary and self._is_idle_boundary(event):
                        done = True
                        break
                else:
                    break

            except WorkflowsException as exc:
                if self._is_retryable_stream_disconnect(exc):
                    retry_count += 1
                    if retry_count > max_retry_count:
                        break
                    await asyncio.sleep(0.2 * retry_count)
                    continue
                raise AgentLoopStateError(str(exc)) from exc
            finally:
                await stream.aclose()

    def _normalize_stream_event(
        self, event: WorkflowEvent | dict[str, Any]
    ) -> WorkflowEvent | None:
        if not isinstance(event, dict):
            return event
        try:
            return _WORKFLOW_EVENT_ADAPTER.validate_python(event)
        except ValidationError:
            return None

    def _consume_workflow_event(self, event: WorkflowEvent) -> list[BaseEvent]:
        return self._translator.consume_workflow_event(event)

    def _is_retryable_stream_disconnect(self, exc: WorkflowsException) -> bool:
        if exc.code != ErrorCode.GET_EVENTS_STREAM_ERROR:
            return False

        msg = str(exc).lower()
        return any(needle in msg for needle in _RETRYABLE_STREAM_ERRORS)

    def _is_idle_boundary(self, event: WorkflowEvent) -> bool:
        return self._translator.is_idle_boundary(event)

    def _merge_message(self, message: LLMMessage) -> None:
        if not self.messages:
            self.messages.append(message)
            return

        last_message = self.messages[-1]
        if (
            last_message.role == message.role
            and last_message.message_id == message.message_id
            and message.role == Role.assistant
        ):
            self.messages[-1] = last_message + message
            return

        self.messages.append(message)
