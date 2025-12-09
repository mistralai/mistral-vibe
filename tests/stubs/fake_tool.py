from __future__ import annotations

from pydantic import BaseModel

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState


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

    async def run(self, args: FakeToolArgs) -> FakeToolResult:
        if self._exception_to_raise:
            raise self._exception_to_raise
        return FakeToolResult()
