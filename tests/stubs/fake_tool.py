from __future__ import annotations

from collections.abc import AsyncGenerator

from pydantic import BaseModel

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, InvokeContext
from vibe.core.types import ToolStreamEvent


class FakeToolArgs(BaseModel):
    pass


class FakeToolResult(BaseModel):
    message: str = "fake tool executed"


class FakeToolState(BaseToolState):
    pass


class FakeTool(BaseTool[FakeToolArgs, FakeToolResult, BaseToolConfig, FakeToolState]):
    _exception_to_raise: BaseException | None = None

    @classmethod
    def get_name(cls) -> str:
        return "stub_tool"

    async def run(
        self, args: FakeToolArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | FakeToolResult, None]:
        if self._exception_to_raise:
            raise self._exception_to_raise
        yield FakeToolResult()
