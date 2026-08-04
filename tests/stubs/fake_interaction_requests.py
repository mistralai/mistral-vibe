from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from vibe.core.tools.base import ToolError
from vibe.core.tools.models import RequiredPermission
from vibe.core.types import ApprovalResponse

type ApprovalRequestHandler = Callable[
    [str, BaseModel, str, list[RequiredPermission] | None],
    Awaitable[tuple[ApprovalResponse, str | None]],
]
type UserInputRequestHandler = Callable[[BaseModel], Awaitable[BaseModel]]


class FakeInteractionRequests:
    def __init__(
        self,
        *,
        approval: ApprovalRequestHandler | None = None,
        user_input: UserInputRequestHandler | None = None,
    ) -> None:
        self._approval = approval
        self._user_input = user_input

    async def request_approval(
        self,
        tool_name: str,
        args: BaseModel,
        tool_call_id: str,
        required_permissions: list[RequiredPermission] | None,
    ) -> tuple[ApprovalResponse, str | None]:
        if self._approval is None:
            return ApprovalResponse.NO, "Tool execution not permitted."
        return await self._approval(tool_name, args, tool_call_id, required_permissions)

    async def request_user_input(self, args: BaseModel, tool_call_id: str) -> BaseModel:
        del tool_call_id
        if self._user_input is None:
            raise ToolError("User input is not available")
        return await self._user_input(args)
