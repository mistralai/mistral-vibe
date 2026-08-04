from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolResultEvent
from vibe.questions import (
    UserQuestionRequest as AskUserQuestionArgs,
    UserQuestionResult as AskUserQuestionResult,
)
from vibe.utils.tool_presentation import ToolEffectKind


class AskUserQuestionConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class AskUserQuestion(
    BaseTool[
        AskUserQuestionArgs, AskUserQuestionResult, AskUserQuestionConfig, BaseToolState
    ],
    ToolUIData[AskUserQuestionArgs, AskUserQuestionResult],
):
    effect_kind = ToolEffectKind.USER_QUESTION

    @classmethod
    def format_call_display(cls, args: AskUserQuestionArgs) -> ToolCallDisplay:
        count = len(args.questions)
        if count == 1:
            question = args.questions[0].question
            return ToolCallDisplay(
                summary=f"Asking: {question}",
                verb="Asking",
                message=question,
                settled_verb="Asked",
                settled_message=question,
            )
        return ToolCallDisplay(
            summary=f"Asking {count} questions",
            verb="Asking",
            message=f"{count} questions",
            settled_verb="Asked",
            settled_message=f"{count} questions",
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)

        result = event.result
        if not isinstance(result, AskUserQuestionResult):
            return ToolResultDisplay(success=True, verb="Answered", message="")

        if result.cancelled:
            return ToolResultDisplay(success=False, verb="Cancelled", message="by user")

        parts = [
            f'"{a.question}" → {"(Other) " if a.is_other else ""}{a.answer}'
            for a in result.answers
        ]
        return ToolResultDisplay(
            success=True, verb="Answered", message=" · ".join(parts)
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Waiting for user input"

    async def run(
        self, args: AskUserQuestionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[AskUserQuestionResult, None]:
        if ctx is None or ctx.interaction_requests is None:
            raise ToolError(
                "User input not available. This tool requires an interactive UI."
            )

        result = await ctx.interaction_requests.request_user_input(
            args, ctx.tool_call_id
        )
        yield cast(AskUserQuestionResult, result)
