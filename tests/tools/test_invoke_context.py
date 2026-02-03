from __future__ import annotations

from collections.abc import AsyncGenerator

from pydantic import BaseModel
import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, InvokeContext
from vibe.core.types import ApprovalCallback, ApprovalResponse, ToolStreamEvent


class SimpleArgs(BaseModel):
    value: str


class SimpleResult(BaseModel):
    output: str
    had_context: bool
    approval_callback_present: bool


class SimpleTool(BaseTool[SimpleArgs, SimpleResult, BaseToolConfig, BaseToolState]):
    description = "A simple test tool"

    async def run(
        self, args: SimpleArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | SimpleResult, None]:
        yield SimpleResult(
            output=f"processed: {args.value}",
            had_context=ctx is not None,
            approval_callback_present=ctx is not None
            and ctx.approval_callback is not None,
        )


@pytest.fixture
def simple_tool() -> SimpleTool:
    return SimpleTool(config=BaseToolConfig(), state=BaseToolState())


class TestInvokeContext:
    def test_default_approval_callback_is_none(self) -> None:
        ctx = InvokeContext(tool_call_id="test-call-id")
        assert ctx.approval_callback is None

    def test_approval_callback_can_be_set(self) -> None:
        def dummy_callback(
            _tool_name: str, _args: BaseModel, _tool_call_id: str
        ) -> tuple[ApprovalResponse, str | None]:
            return ApprovalResponse.YES, None

        callback: ApprovalCallback = dummy_callback
        ctx = InvokeContext(tool_call_id="test-call-id", approval_callback=callback)
        assert ctx.approval_callback is callback


class TestToolInvokeWithContext:
    @pytest.mark.asyncio
    async def test_invoke_without_context(self, simple_tool: SimpleTool) -> None:
        result = await collect_result(simple_tool.invoke(value="test"))

        assert result.output == "processed: test"
        assert result.had_context is False
        assert result.approval_callback_present is False

    @pytest.mark.asyncio
    async def test_invoke_with_empty_context(self, simple_tool: SimpleTool) -> None:
        ctx = InvokeContext(tool_call_id="test-call-id")
        result = await collect_result(simple_tool.invoke(ctx=ctx, value="test"))

        assert result.output == "processed: test"
        assert result.had_context is True
        assert result.approval_callback_present is False

    @pytest.mark.asyncio
    async def test_invoke_with_approval_callback(self, simple_tool: SimpleTool) -> None:
        def dummy_callback(
            _tool_name: str, _args: BaseModel, _tool_call_id: str
        ) -> tuple[ApprovalResponse, str | None]:
            return ApprovalResponse.YES, None

        callback: ApprovalCallback = dummy_callback
        ctx = InvokeContext(tool_call_id="test-call-id", approval_callback=callback)
        result = await collect_result(simple_tool.invoke(ctx=ctx, value="test"))

        assert result.output == "processed: test"
        assert result.had_context is True
        assert result.approval_callback_present is True

    @pytest.mark.asyncio
    async def test_run_receives_context(self, simple_tool: SimpleTool) -> None:
        ctx = InvokeContext(tool_call_id="test-call-id")
        result = await collect_result(
            simple_tool.run(SimpleArgs(value="direct"), ctx=ctx)
        )

        assert result.had_context is True

    @pytest.mark.asyncio
    async def test_run_without_context_defaults_to_none(
        self, simple_tool: SimpleTool
    ) -> None:
        result = await collect_result(simple_tool.run(SimpleArgs(value="direct")))

        assert result.had_context is False
