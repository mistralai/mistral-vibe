from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel

from vibe.core.tools.base import InteractionRequestPort, ToolError
from vibe.core.tools.models import RequiredPermission
from vibe.core.types import (
    ApprovalRequestEvent,
    ApprovalResponse,
    BaseEvent,
    UserInputRequestEvent,
)


@dataclass(frozen=True, slots=True)
class ApprovalResolution:
    response: ApprovalResponse
    feedback: str | None


class InteractionRequestBroker(InteractionRequestPort):
    def __init__(self) -> None:
        self._events: asyncio.Queue[BaseEvent | None] | None = None
        self._approval_requests: dict[str, asyncio.Future[ApprovalResolution]] = {}
        self._user_input_requests: dict[str, asyncio.Future[BaseModel]] = {}

    def bind(self, events: asyncio.Queue[BaseEvent | None]) -> None:
        if self._events is not None:
            raise RuntimeError("An interaction request stream is already active")
        self._events = events

    def unbind(self, events: asyncio.Queue[BaseEvent | None]) -> None:
        if self._events is not events:
            raise RuntimeError("Cannot close an inactive interaction request stream")
        self._events = None

    async def request_approval(
        self,
        tool_name: str,
        args: BaseModel,
        tool_call_id: str,
        required_permissions: list[RequiredPermission] | None,
    ) -> tuple[ApprovalResponse, str | None]:
        events = self._events
        if events is None:
            return ApprovalResponse.NO, "Tool execution not permitted."

        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._approval_requests[request_id] = future
        await events.put(
            ApprovalRequestEvent(
                request_id=request_id,
                tool_name=tool_name,
                tool_args=args,
                tool_call_id=tool_call_id,
                required_permissions=required_permissions,
            )
        )
        try:
            resolution = await future
            return resolution.response, resolution.feedback
        finally:
            self._approval_requests.pop(request_id, None)

    async def request_user_input(self, args: BaseModel, tool_call_id: str) -> BaseModel:
        events = self._events
        if events is None:
            raise ToolError("User input is not available outside an active turn")

        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._user_input_requests[request_id] = future
        await events.put(
            UserInputRequestEvent(
                request_id=request_id, args=args, tool_call_id=tool_call_id
            )
        )
        try:
            return await future
        finally:
            self._user_input_requests.pop(request_id, None)

    def resolve_approval(
        self, request_id: str, response: ApprovalResponse, feedback: str | None
    ) -> None:
        future = self._approval_requests.get(request_id)
        if future is not None and not future.done():
            future.set_result(ApprovalResolution(response, feedback))

    def resolve_user_input(self, request_id: str, result: BaseModel) -> None:
        future = self._user_input_requests.get(request_id)
        if future is not None and not future.done():
            future.set_result(result)

    def reject(self, request_id: str, error: BaseException) -> None:
        future: asyncio.Future[ApprovalResolution] | asyncio.Future[BaseModel] | None
        future = self._approval_requests.get(request_id)
        if future is None:
            future = self._user_input_requests.get(request_id)
        if future is not None and not future.done():
            future.set_exception(error)
