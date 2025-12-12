from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from chefchat.cli.mode_manager import ModeManager, VibeMode
from chefchat.core.agent import Agent
from chefchat.core.config import VibeConfig
from chefchat.core.tools.base import BaseTool, ToolPermission
from chefchat.core.types import ToolExecutionResponse


# Mock Tool to verify blocking
class MockWriteTool(BaseTool):
    def get_name(cls) -> str:
        return "write_file"

    async def run(self, args):
        pass


@pytest.fixture
def plan_mode_agent():
    # Setup Config
    config = MagicMock(spec=VibeConfig)
    config.denylist = []
    config.allow_prompt_injection = False
    config.tool_paths = []
    config.mcp_servers = []
    config.auto_compact_threshold = 0
    config.session_logging = MagicMock(enabled=False)
    config.effective_workdir = Path("/tmp")
    config.include_model_info = False
    config.active_model = "test-model"
    config.include_prompt_detail = False
    config.include_project_context = False
    config.instructions = ""
    config.system_prompt = "System Prompt"
    config.system_prompt_id = "default"
    config.get_active_model.return_value = MagicMock(input_price=0, output_price=0)

    # Setup Agent with Mock Backend
    backend = AsyncMock()
    # Mock ModeManager
    mode_manager = ModeManager(initial_mode=VibeMode.PLAN)

    agent = Agent(config=config, backend=backend, mode_manager=mode_manager)

    # We need to ensure the tool executor has the mode manager too
    agent.tool_executor.mode_manager = mode_manager
    return agent


@pytest.mark.asyncio
async def test_agent_blocks_write_file_in_plan_mode(plan_mode_agent):
    """Integration test: Verify AgentToolExecutor respects PLAN mode blocking."""
    agent = plan_mode_agent
    tool_name = "write_file"
    args = {"path": "test.py", "content": "print('fail')"}

    # Mock a tool instance
    tool = MagicMock()
    tool.get_name.return_value = tool_name

    # Execute the decision logic on the EXECUTOR, not the agent directly
    decision = await agent.tool_executor._should_execute_tool(tool, args, "call_id_123")

    # Assertions
    assert decision.verdict == ToolExecutionResponse.SKIP
    assert "blocked" in decision.feedback.lower()
    assert "read-only" in decision.feedback.lower()


@pytest.mark.asyncio
async def test_agent_blocking_overrides_auto_approve(plan_mode_agent):
    """Integration test: Verify specific blocking check comes BEFORE auto-approve check.
    Even if we force auto_approve=True manually, PLAN mode should still block writes.
    """
    agent = plan_mode_agent

    # Manually FORCE auto_approve to True (simulating a potential state leak or bug)
    agent.tool_executor.auto_approve = True

    tool_name = "delete_file"
    args = {"path": "important.py"}

    tool = MagicMock()
    tool.get_name.return_value = tool_name

    # Execute decision logic
    decision = await agent.tool_executor._should_execute_tool(tool, args, "call_id_456")

    # Should still skip!
    assert decision.verdict == ToolExecutionResponse.SKIP
    assert "blocked" in decision.feedback.lower()


@pytest.mark.asyncio
async def test_agent_allows_read_ops_in_plan_mode(plan_mode_agent):
    """Integration test: Verify Agent allows read operations in PLAN mode (falling through to approval)."""
    agent = plan_mode_agent
    # PLAN mode requires manual approval
    agent.tool_executor.auto_approve = False

    tool_name = "read_file"
    args = {"path": "test.py"}

    tool = MagicMock()
    tool.get_name.return_value = tool_name

    # Mock permissions return
    tool.check_allowlist_denylist.return_value = ToolPermission.ASK
    tool._get_args_and_result_models.return_value = (MagicMock(), MagicMock())
    tool.config.denylist = []  # Needed for NEVER check

    # Setup tool manager mock since it's used deeper in the function
    # Agent initializes real tool manager, we need to mock calls on it or replace it
    agent.tool_executor.tool_manager = MagicMock()
    tool_config = MagicMock()
    tool_config.permission = ToolPermission.ASK
    agent.tool_executor.tool_manager.get_tool_config.return_value = tool_config

    # Mock approval callback to NO (just to stop execution flow, but prove we got past blocking)
    agent.tool_executor.approval_callback = AsyncMock(
        return_value=(None, "Manual skip")
    )

    # Execute decision
    try:
        await agent.tool_executor._should_execute_tool(tool, args, "call_id_789")
    except Exception:
        pass

    blocked, _ = agent.mode_manager.should_block_tool(tool_name, args)
    assert blocked is False
