from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
import pytest

from tests.mock.utils import collect_result
from vibe.core.agents.models import AgentProfile, AgentSafety, BuiltinAgentName
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError
from vibe.core.tools.builtins.ask_user_question import (
    Answer,
    AskUserQuestionArgs,
    AskUserQuestionResult,
)
from vibe.core.tools.builtins.exit_plan_mode import (
    ExitPlanMode,
    ExitPlanModeArgs,
    ExitPlanModeConfig,
)


@dataclass
class MockAgentManager:
    active_profile: AgentProfile
    _switched_to: list[str] = field(default_factory=list)

    def switch_profile(self, name: str) -> None:
        self._switched_to.append(name)
        self.active_profile = AgentProfile(
            name=name,
            display_name=name.title(),
            description="",
            safety=AgentSafety.SAFE,
        )


def _plan_profile() -> AgentProfile:
    return AgentProfile(
        name=BuiltinAgentName.PLAN,
        display_name="Plan",
        description="Plan mode",
        safety=AgentSafety.SAFE,
    )


def _default_profile() -> AgentProfile:
    return AgentProfile(
        name=BuiltinAgentName.DEFAULT,
        display_name="Default",
        description="Default mode",
        safety=AgentSafety.SAFE,
    )


@pytest.fixture
def tool() -> ExitPlanMode:
    return ExitPlanMode(
        config_getter=lambda: ExitPlanModeConfig(), state=BaseToolState()
    )


@pytest.fixture
def plan_manager() -> MockAgentManager:
    return MockAgentManager(active_profile=_plan_profile())


class MockCallback:
    def __init__(self, result: AskUserQuestionResult) -> None:
        self._result = result
        self.received_args: BaseModel | None = None

    async def __call__(self, args: BaseModel) -> BaseModel:
        self.received_args = args
        return self._result


class TestErrorCases:
    @pytest.mark.asyncio
    async def test_requires_agent_manager(self, tool: ExitPlanMode) -> None:
        ctx = InvokeContext(
            tool_call_id="t1",
            user_input_callback=MockCallback(
                AskUserQuestionResult(answers=[], cancelled=True)
            ),
        )
        with pytest.raises(ToolError, match="agent manager"):
            await collect_result(tool.run(ExitPlanModeArgs(), ctx))

    @pytest.mark.asyncio
    async def test_requires_plan_mode(self, tool: ExitPlanMode) -> None:
        manager = MockAgentManager(active_profile=_default_profile())
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=manager,  # type: ignore[arg-type]
            user_input_callback=MockCallback(
                AskUserQuestionResult(answers=[], cancelled=True)
            ),
        )
        with pytest.raises(ToolError, match="plan mode"):
            await collect_result(tool.run(ExitPlanModeArgs(), ctx))

    @pytest.mark.asyncio
    async def test_requires_interactive_ui(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager
    ) -> None:
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
        )
        with pytest.raises(ToolError, match="interactive UI"):
            await collect_result(tool.run(ExitPlanModeArgs(), ctx))


class MockSwitchAgentCallback:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, name: str) -> None:
        self.calls.append(name)


class TestAnswerHandling:
    @pytest.mark.asyncio
    async def test_yes_uses_switch_agent_callback(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager
    ) -> None:
        switch_cb = MockSwitchAgentCallback()
        cb = MockCallback(
            AskUserQuestionResult(
                answers=[
                    Answer(
                        question="q",
                        answer="Yes, and auto approve edits",
                        is_other=False,
                    )
                ],
                cancelled=False,
            )
        )
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
            user_input_callback=cb,
            switch_agent_callback=switch_cb,
        )
        result = await collect_result(tool.run(ExitPlanModeArgs(), ctx))
        assert result.switched is True
        assert switch_cb.calls == [BuiltinAgentName.ACCEPT_EDITS]
        assert plan_manager._switched_to == []

    @pytest.mark.asyncio
    async def test_yes_falls_back_to_switch_profile(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager
    ) -> None:
        cb = MockCallback(
            AskUserQuestionResult(
                answers=[
                    Answer(
                        question="q",
                        answer="Yes, and auto approve edits",
                        is_other=False,
                    )
                ],
                cancelled=False,
            )
        )
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
            user_input_callback=cb,
        )
        result = await collect_result(tool.run(ExitPlanModeArgs(), ctx))
        assert result.switched is True
        assert plan_manager._switched_to == [BuiltinAgentName.ACCEPT_EDITS]

    @pytest.mark.asyncio
    async def test_no_stays_in_plan_mode(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager
    ) -> None:
        cb = MockCallback(
            AskUserQuestionResult(
                answers=[Answer(question="q", answer="No", is_other=False)],
                cancelled=False,
            )
        )
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
            user_input_callback=cb,
        )
        result = await collect_result(tool.run(ExitPlanModeArgs(), ctx))
        assert result.switched is False
        assert plan_manager._switched_to == []

    @pytest.mark.asyncio
    async def test_cancelled_stays(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager
    ) -> None:
        cb = MockCallback(AskUserQuestionResult(answers=[], cancelled=True))
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
            user_input_callback=cb,
        )
        result = await collect_result(tool.run(ExitPlanModeArgs(), ctx))
        assert result.switched is False
        assert plan_manager._switched_to == []

    @pytest.mark.asyncio
    async def test_other_includes_feedback(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager
    ) -> None:
        cb = MockCallback(
            AskUserQuestionResult(
                answers=[
                    Answer(question="q", answer="Add error handling", is_other=True)
                ],
                cancelled=False,
            )
        )
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
            user_input_callback=cb,
        )
        result = await collect_result(tool.run(ExitPlanModeArgs(), ctx))
        assert result.switched is False
        assert "Add error handling" in result.message


class TestPlanFile:
    @pytest.mark.asyncio
    async def test_content_passed_as_preview(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager, tmp_path: Path
    ) -> None:
        plan_file = tmp_path / "plan.md"
        plan_file.write_text("# My Plan\n\n- Step 1\n- Step 2\n")

        cb = MockCallback(AskUserQuestionResult(answers=[], cancelled=True))
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
            user_input_callback=cb,
            plan_file_path=plan_file,
        )
        await collect_result(tool.run(ExitPlanModeArgs(), ctx))
        assert isinstance(cb.received_args, AskUserQuestionArgs)
        assert cb.received_args.content_preview == "# My Plan\n\n- Step 1\n- Step 2\n"

    @pytest.mark.asyncio
    async def test_missing_file_means_none_preview(
        self, tool: ExitPlanMode, plan_manager: MockAgentManager, tmp_path: Path
    ) -> None:
        plan_file = tmp_path / "nonexistent.md"

        cb = MockCallback(AskUserQuestionResult(answers=[], cancelled=True))
        ctx = InvokeContext(
            tool_call_id="t1",
            agent_manager=plan_manager,  # type: ignore[arg-type]
            user_input_callback=cb,
            plan_file_path=plan_file,
        )
        await collect_result(tool.run(ExitPlanModeArgs(), ctx))
        assert isinstance(cb.received_args, AskUserQuestionArgs)
        assert cb.received_args.content_preview is None
