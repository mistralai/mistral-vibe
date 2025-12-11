from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pydantic import BaseModel
import pytest

from vibe.cli.mode_manager import ModeManager, VibeMode
from vibe.core.agent_tool_executor import AgentToolExecutor
from vibe.core.config import VibeConfig
from vibe.core.tools.base import BaseTool, ToolPermission
from vibe.core.tools.manager import ToolManager
from vibe.core.types import Role, ToolCallEvent, ToolResultEvent


# Restrict anyio to asyncio
@pytest.fixture
def anyio_backend():
    return "asyncio"


# Mocks
class MockToolCall:
    def __init__(self, tool_name, args, call_id="call_1"):
        self.tool_name = tool_name
        self.args_dict = args
        self.validated_args = args
        self.call_id = call_id

        # Create a dummy class inheriting from BaseTool to satisfy Pydantic
        class DummyTool(BaseTool):
            pass

        self.tool_class = DummyTool


class MockResolvedMessage:
    def __init__(self, tool_calls, failed_calls=None):
        self.tool_calls = tool_calls
        self.failed_calls = failed_calls or []


@pytest.fixture
def executor_setup():
    config = MagicMock(spec=VibeConfig)
    config.denylist = []

    tool_manager = MagicMock(spec=ToolManager)
    format_handler = MagicMock()
    mode_manager = MagicMock(spec=ModeManager)

    executor = AgentToolExecutor(
        config=config,
        tool_manager=tool_manager,
        format_handler=format_handler,
        mode_manager=mode_manager,
        auto_approve=False,
    )

    return executor, tool_manager, mode_manager


@pytest.mark.anyio
async def test_handle_tool_calls_success(executor_setup):
    executor, tool_manager, mode_manager = executor_setup

    # Setup
    mode_manager.should_block_tool.return_value = (False, None)

    # Define dummy result model
    class DummyResult(BaseModel):
        result: str

    # Use MagicMock for tool, but make invoke async
    tool_instance = MagicMock()
    tool_instance.invoke = AsyncMock()
    tool_instance.invoke.return_value = DummyResult(result="success")

    # Ensure synchronous methods return values, not coroutines
    tool_instance._get_args_and_result_models.return_value = (MagicMock(), MagicMock())
    tool_instance.check_allowlist_denylist.return_value = ToolPermission.ALWAYS

    tool_config = MagicMock()
    tool_config.permission = ToolPermission.ALWAYS
    tool_manager.get_tool_config.return_value = tool_config

    tool_manager.get.return_value = tool_instance

    # Mock format_handler to return valid dict for LLMMessage
    executor.format_handler.create_tool_response_message.return_value = {
        "role": Role.tool,
        "content": "Tool output",
        "tool_call_id": "call_1",
        "name": "test_tool",
    }

    tool_call = MockToolCall("test_tool", {"arg": 1})
    resolved = MockResolvedMessage([tool_call])

    messages = []
    stats = MagicMock()
    logger = MagicMock()

    # Execute
    events = []
    async for event in executor.handle_tool_calls(resolved, messages, stats, logger):
        events.append(event)

    # Verify
    assert len(events) == 2
    assert isinstance(events[0], ToolCallEvent)
    assert isinstance(events[1], ToolResultEvent)
    assert events[1].result is not None
    assert messages


@pytest.mark.anyio
async def test_handle_tool_calls_blocked_by_mode(executor_setup):
    executor, tool_manager, mode_manager = executor_setup

    # Setup blocking
    mode_manager.should_block_tool.return_value = (True, "Blocked by test")
    mode_manager.current_mode = VibeMode.PLAN

    tool_instance = MagicMock()
    tool_manager.get.return_value = tool_instance

    # Fix LLMMessage validation
    executor.format_handler.create_tool_response_message.return_value = {
        "role": Role.tool,
        "content": "Blocked",
        "tool_call_id": "call_1",
        "name": "write_file",
    }

    tool_call = MockToolCall("write_file", {"path": "test.txt"})
    resolved = MockResolvedMessage([tool_call])

    messages = []
    stats = MagicMock()
    logger = MagicMock()

    # Execute
    events = []
    async for event in executor.handle_tool_calls(resolved, messages, stats, logger):
        events.append(event)

    # Verify
    assert len(events) == 2
    assert events[1].skipped is True
    assert "BLOCKED" in events[1].skip_reason
    assert "PLAN" in events[1].skip_reason


@pytest.mark.anyio
async def test_handle_tool_calls_requires_approval(executor_setup):
    executor, tool_manager, mode_manager = executor_setup

    mode_manager.should_block_tool.return_value = (False, None)

    class DummyResult(BaseModel):
        result: str

    # Use MagicMock for tool to avoid async issues with sync methods
    tool_instance = MagicMock()
    tool_instance.invoke = AsyncMock()
    tool_instance.invoke.return_value = DummyResult(result="ok")

    tool_instance._get_args_and_result_models.return_value = (MagicMock(), MagicMock())
    tool_instance.check_allowlist_denylist.return_value = None  # Not ALWAYS/NEVER

    tool_config = MagicMock()
    tool_config.permission = None  # Not set
    tool_manager.get_tool_config.return_value = tool_config

    tool_manager.get.return_value = tool_instance

    # Mock format_handler
    executor.format_handler.create_tool_response_message.return_value = {
        "role": Role.tool,
        "content": "Tool output",
        "tool_call_id": "call_1",
        "name": "test_tool",
    }

    # Callback approves
    async def approval_cb(name, args, id):
        from vibe.core.utils import ApprovalResponse

        return ApprovalResponse.YES, "Approved"

    executor.set_approval_callback(approval_cb)

    tool_call = MockToolCall("test_tool", {})
    resolved = MockResolvedMessage([tool_call])

    messages = []
    stats = MagicMock()
    logger = MagicMock()

    events = []
    async for event in executor.handle_tool_calls(resolved, messages, stats, logger):
        events.append(event)

    assert len(events) == 2
    assert events[1].result is not None
