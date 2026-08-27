from __future__ import annotations

from vibe.core.subagents import SubagentRunAccumulator, TaskResult
from vibe.core.tools.builtins.bash import Bash, CapturedShellResult
from vibe.core.types import AssistantEvent, ToolResultEvent


def test_subagent_run_accumulates_response_and_tool_progress() -> None:
    accumulator = SubagentRunAccumulator()

    assert (
        accumulator.observe(
            AssistantEvent(content="Found the issue"), tool_call_id="task-1"
        )
        is None
    )
    progress = accumulator.observe(
        ToolResultEvent(
            tool_name="bash",
            tool_class=Bash,
            result=CapturedShellResult(command="pwd", stdout="/repo", stderr=""),
            tool_call_id="bash-1",
        ),
        tool_call_id="task-1",
    )

    assert progress is not None
    assert progress.tool_call_id == "task-1"
    assert progress.message == "bash: Ran pwd"
    assert accumulator.build_result(turns_used=1) == TaskResult(
        response="Found the issue", turns_used=1, completed=True
    )


def test_subagent_run_combines_observed_and_runtime_failures() -> None:
    accumulator = SubagentRunAccumulator()
    accumulator.observe(
        AssistantEvent(content="Partial", stopped_by_middleware=True),
        tool_call_id="task-1",
    )
    accumulator.record_error("child failed")

    assert accumulator.build_result(turns_used=2, completed=False) == TaskResult(
        response="Partial\n[Subagent error: child failed]",
        turns_used=2,
        completed=False,
    )
