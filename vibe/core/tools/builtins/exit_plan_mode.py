from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar, cast

from pydantic import BaseModel

from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    AskUserQuestionResult,
    Choice,
    Question,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.utils.io import read_safe


class ExitPlanModeArgs(BaseModel):
    pass


class ExitPlanModeResult(BaseModel):
    switched: bool
    message: str


class ExitPlanModeConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class ExitPlanMode(
    BaseTool[ExitPlanModeArgs, ExitPlanModeResult, ExitPlanModeConfig, BaseToolState],
    ToolUIData[ExitPlanModeArgs, ExitPlanModeResult],
):
    description: ClassVar[str] = (
        "Signal that your plan is complete and you are ready to start implementing. "
        "This will ask the user to confirm switching from plan mode to accept-edits mode. "
        "Only use this tool when you have finished writing your plan to the plan file "
        "and are ready for user approval to begin implementation."
    )

    @classmethod
    def format_call_display(cls, args: ExitPlanModeArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Ready to exit plan mode")

    @classmethod
    def format_result_display(cls, result: ExitPlanModeResult) -> ToolResultDisplay:
        return ToolResultDisplay(success=result.switched, message=result.message)

    @classmethod
    def get_status_text(cls) -> str:
        return "Waiting for user confirmation"

    async def run(
        self, args: ExitPlanModeArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ExitPlanModeResult, None]:
        if ctx is None or ctx.agent_manager is None:
            raise ToolError("ExitPlanMode requires an agent manager context.")

        if ctx.agent_manager.active_profile.name != BuiltinAgentName.PLAN:
            raise ToolError("ExitPlanMode can only be used in plan mode.")

        if ctx.user_input_callback is None:
            raise ToolError("ExitPlanMode requires an interactive UI.")

        plan_content: str | None = None
        if ctx.plan_file_path and ctx.plan_file_path.is_file():
            try:
                plan_content = read_safe(ctx.plan_file_path).text
            except OSError as e:
                raise ToolError(
                    f"Failed to read plan file at {ctx.plan_file_path}: {e}"
                ) from e

        confirmation = AskUserQuestionArgs(
            questions=[
                Question(
                    question="Plan is complete. Switch to accept-edits mode and start implementing?",
                    header="Plan ready",
                    options=[
                        Choice(
                            label="Yes, and auto approve edits",
                            description="Switch to accept-edits mode with auto-approve permissions",
                        ),
                        Choice(
                            label="Yes, and request approval for edits",
                            description="Switch to default agent mode (manual approval for edits)",
                        ),
                        Choice(
                            label="No",
                            description="Stay in plan mode and continue planning",
                        ),
                    ],
                )
            ],
            content_preview=plan_content,
        )

        result = await ctx.user_input_callback(confirmation)
        result = cast(AskUserQuestionResult, result)

        if result.cancelled or not result.answers:
            yield ExitPlanModeResult(
                switched=False, message="User cancelled. Staying in plan mode."
            )
            return

        answer = result.answers[0]
        answer_lower = answer.answer.lower()
        if answer_lower == "yes, and auto approve edits":
            if ctx.switch_agent_callback:
                await ctx.switch_agent_callback(BuiltinAgentName.ACCEPT_EDITS)
            else:
                ctx.agent_manager.switch_profile(BuiltinAgentName.ACCEPT_EDITS)
            yield ExitPlanModeResult(
                switched=True,
                message="Switched to accept-edits mode. You can now start implementing the plan.",
            )
        elif answer_lower == "yes, and request approval for edits":
            if ctx.switch_agent_callback:
                await ctx.switch_agent_callback(BuiltinAgentName.DEFAULT)
            else:
                ctx.agent_manager.switch_profile(BuiltinAgentName.DEFAULT)
            yield ExitPlanModeResult(
                switched=True,
                message="Switched to default agent mode. Edits will require your approval.",
            )
        elif answer.is_other:
            yield ExitPlanModeResult(
                switched=False,
                message=f"Staying in plan mode. User feedback: {answer.answer}",
            )
        else:
            yield ExitPlanModeResult(
                switched=False,
                message="Staying in plan mode. Continue refining the plan.",
            )
