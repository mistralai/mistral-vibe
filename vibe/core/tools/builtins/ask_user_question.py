from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar, cast

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolCallEvent, ToolResultEvent


class Choice(BaseModel):
    label: str = Field(description="Short label for the choice (1-5 words)")
    description: str = Field(
        default="", description="Optional explanation of this choice"
    )


class Question(BaseModel):
    question: str = Field(description="The question text")
    header: str = Field(
        default="",
        description="Short header for the question (1-2 words, e.g. 'Auth', 'Database')",
        max_length=12,
    )
    options: list[Choice] = Field(
        description="Available options (2-4, not including 'Other'). An 'Other' option for free text is automatically added.",
        min_length=2,
        max_length=4,
    )
    multi_select: bool = Field(
        default=False, description="If true, user can select multiple options"
    )
    hide_other: bool = Field(
        default=False, description="If true, hide the 'Other' free text option"
    )


class AskUserQuestionArgs(BaseModel):
    questions: list[Question] = Field(
        description="Questions to ask (1-4). Displayed as tabs if multiple.",
        min_length=1,
        max_length=4,
    )


class Answer(BaseModel):
    question: str = Field(description="The original question")
    answer: str = Field(description="The user's answer")
    is_other: bool = Field(
        default=False, description="True if user typed a custom answer via 'Other'"
    )


class AskUserQuestionResult(BaseModel):
    answers: list[Answer] = Field(description="List of answers")
    cancelled: bool = Field(
        default=False, description="True if user cancelled without answering"
    )


class AskUserQuestionConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class AskUserQuestion(
    BaseTool[
        AskUserQuestionArgs, AskUserQuestionResult, AskUserQuestionConfig, BaseToolState
    ],
    ToolUIData[AskUserQuestionArgs, AskUserQuestionResult],
):
    description: ClassVar[str] = (
        "Ask the user one or more questions and wait for their responses. "
        "Each question has 2-4 choices plus an automatic 'Other' option for free text. "
        "Use this to gather preferences, clarify requirements, or get decisions."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, AskUserQuestionArgs):
            return ToolCallDisplay(summary="Asking user")

        args = event.args
        count = len(args.questions)

        if count == 1:
            return ToolCallDisplay(summary=f"Asking: {args.questions[0].question}")

        return ToolCallDisplay(summary=f"Asking {count} questions")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)

        if not isinstance(event.result, AskUserQuestionResult):
            return ToolResultDisplay(success=True, message="Questions answered")

        result = event.result

        if result.cancelled:
            return ToolResultDisplay(success=False, message="User cancelled")

        if len(result.answers) == 1:
            answer = result.answers[0]
            prefix = "(Other) " if answer.is_other else ""
            return ToolResultDisplay(success=True, message=f"{prefix}{answer.answer}")

        return ToolResultDisplay(
            success=True, message=f"{len(result.answers)} answers received"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Waiting for user input"

    async def run(
        self, args: AskUserQuestionArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[AskUserQuestionResult, None]:
        if ctx is None or ctx.user_input_callback is None:
            raise ToolError(
                "User input not available. This tool requires an interactive UI."
            )

        result = await ctx.user_input_callback(args)
        yield cast(AskUserQuestionResult, result)
