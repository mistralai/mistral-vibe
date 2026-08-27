from __future__ import annotations

from collections.abc import AsyncGenerator

from pydantic import BaseModel
import pytest

from tests.mock.utils import collect_result
from tests.stubs.fake_interaction_requests import FakeInteractionRequests
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, InvokeContext
from vibe.core.types import ApprovalResponse, ToolStreamEvent


class SimpleArgs(BaseModel):
    value: str


class SimpleResult(BaseModel):
    output: str
    had_context: bool
    interaction_requests_present: bool


class SimpleTool(BaseTool[SimpleArgs, SimpleResult, BaseToolConfig, BaseToolState]):
    description = "A simple test tool"

    async def run(
        self, args: SimpleArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | SimpleResult, None]:
        yield SimpleResult(
            output=f"processed: {args.value}",
            had_context=ctx is not None,
            interaction_requests_present=ctx is not None
            and ctx.interaction_requests is not None,
        )


@pytest.fixture
def simple_tool() -> SimpleTool:
    return SimpleTool(config_getter=lambda: BaseToolConfig(), state=BaseToolState())


class TestInvokeContext:
    def test_default_interaction_requests_is_none(self) -> None:
        ctx = InvokeContext(tool_call_id="test-call-id")
        assert ctx.interaction_requests is None

    def test_interaction_requests_can_be_set(self) -> None:
        async def approve(
            _tool_name: str,
            _args: BaseModel,
            _tool_call_id: str,
            _rp: list | None = None,
        ) -> tuple[ApprovalResponse, str | None]:
            return ApprovalResponse.YES, None

        requests = FakeInteractionRequests(approval=approve)
        ctx = InvokeContext(tool_call_id="test-call-id", interaction_requests=requests)
        assert ctx.interaction_requests is requests


class TestToolInvokeWithContext:
    @pytest.mark.asyncio
    async def test_invoke_without_context(self, simple_tool: SimpleTool) -> None:
        result = await collect_result(simple_tool.invoke(value="test"))

        assert result.output == "processed: test"
        assert result.had_context is False
        assert result.interaction_requests_present is False

    @pytest.mark.asyncio
    async def test_invoke_with_empty_context(self, simple_tool: SimpleTool) -> None:
        ctx = InvokeContext(tool_call_id="test-call-id")
        result = await collect_result(simple_tool.invoke(ctx=ctx, value="test"))

        assert result.output == "processed: test"
        assert result.had_context is True
        assert result.interaction_requests_present is False

    @pytest.mark.asyncio
    async def test_invoke_with_interaction_requests(
        self, simple_tool: SimpleTool
    ) -> None:
        async def approve(
            _tool_name: str,
            _args: BaseModel,
            _tool_call_id: str,
            _rp: list | None = None,
        ) -> tuple[ApprovalResponse, str | None]:
            return ApprovalResponse.YES, None

        ctx = InvokeContext(
            tool_call_id="test-call-id",
            interaction_requests=FakeInteractionRequests(approval=approve),
        )
        result = await collect_result(simple_tool.invoke(ctx=ctx, value="test"))

        assert result.output == "processed: test"
        assert result.had_context is True
        assert result.interaction_requests_present is True

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
