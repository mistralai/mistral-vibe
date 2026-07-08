from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

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

LABEL_CLEAR_AUTO = "Yes, clear context and auto approve edits"
LABEL_AUTO = "Yes, and auto approve edits"
LABEL_MANUAL = "Yes, and request approval for edits"
LABEL_NO = "No"


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

        options = [
            Choice(
                label=LABEL_CLEAR_AUTO,
                description="Clear the planning context, then switch to accept-edits mode",
            ),
            Choice(
                label=LABEL_AUTO,
                description="Switch to accept-edits mode with auto-approve permissions",
            ),
            Choice(
                label=LABEL_MANUAL,
                description="Switch to default agent mode (manual approval for edits)",
            ),
            Choice(
                label=LABEL_NO, description="Stay in plan mode and continue planning"
            ),
        ]

        plan_path = str(ctx.plan_file_path) if ctx.plan_file_path else ""
        confirmation = AskUserQuestionArgs(
            footer_note=f"Plan: {plan_path} (Ctrl+G to edit)",
            questions=[
                Question(
                    question="Plan is complete. Switch to accept-edits mode and start implementing?",
                    header="Plan ready",
                    options=options,
                )
            ],
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
        is_clear = answer_lower == LABEL_CLEAR_AUTO.lower()
        if answer_lower in {LABEL_CLEAR_AUTO.lower(), LABEL_AUTO.lower()}:
            target = BuiltinAgentName.ACCEPT_EDITS
            base_message = "Switched to accept-edits mode. You can now start implementing the plan."
            clear_message = (
                "Switched to accept-edits mode. Clearing the planning context and "
                "starting implementation from the approved plan."
            )
        elif answer_lower == LABEL_MANUAL.lower():
            target = BuiltinAgentName.DEFAULT
            base_message = (
                "Switched to default agent mode. Edits will require your approval."
            )
            clear_message = base_message
        elif answer.is_other:
            yield ExitPlanModeResult(
                switched=False,
                message=f"Staying in plan mode. User feedback: {answer.answer}",
            )
            return
        else:
            yield ExitPlanModeResult(
                switched=False,
                message="Staying in plan mode. Continue refining the plan.",
            )
            return

        if ctx.switch_agent_callback:
            await ctx.switch_agent_callback(target)
        else:
            ctx.agent_manager.switch_profile(target)

        if is_clear and ctx.request_clear_context_callback is not None:
            await ctx.request_clear_context_callback()

        yield ExitPlanModeResult(
            switched=True, message=clear_message if is_clear else base_message
        )
