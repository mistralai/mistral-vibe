from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolCallEvent, ToolResultEvent


class AskUserChoice(BaseModel):
    """A choice option for a question."""

    label: str = Field(description="Short label for the choice (1-5 words)")
    description: str | None = Field(
        default=None, description="Optional description explaining this choice"
    )


class Question(BaseModel):
    """A single question to ask the user."""

    question: str = Field(description="The question to ask the user")
    header: str = Field(
        description="Short header/title for the question tab (1-2 words, e.g. 'Auth', 'Database')",
        max_length=15,
    )
    choices: list[AskUserChoice] = Field(
        description="List of available options (2-4 choices). An 'Other' option with free text is always added automatically.",
        min_length=2,
        max_length=4,
    )
    multi_select: bool = Field(
        default=False,
        description="If True, user can select multiple options. Enter toggles selection, Tab moves to next question.",
    )
    recommended_index: int | None = Field(
        default=None,
        description="Index (0-based) of the recommended choice. If set, that choice will be marked as '(Recommended)'.",
    )


class AskUserArgs(BaseModel):
    """Arguments for asking the user one or more questions."""

    questions: list[Question] = Field(
        description="List of questions to ask (1-6 questions). Questions appear as tabs, user navigates with Tab/arrows.",
        min_length=1,
        max_length=6,
    )


class Answer(BaseModel):
    """A single answer from the user."""

    question: str = Field(description="The original question")
    answer: str = Field(description="The user's answer")
    is_other: bool = Field(
        default=False,
        description="True if user selected 'Other' and typed custom answer",
    )


class AskUserResult(BaseModel):
    """Result from asking the user questions."""

    answers: list[Answer] = Field(description="List of answers, one per question")
    answered: bool = Field(
        default=True, description="False if user cancelled without answering"
    )

    @property
    def first_answer(self) -> str:
        """Get the first answer (convenience for single question)."""
        return self.answers[0].answer if self.answers else ""

    def to_response_text(self) -> str:
        """Format the result for the LLM response."""
        if not self.answered:
            return "User cancelled the question(s) without answering."

        if not self.answers:
            return "No answers provided."

        lines = []
        for answer in self.answers:
            prefix = "(custom) " if answer.is_other else ""
            lines.append(f"Q: {answer.question}\nA: {prefix}{answer.answer}")

        return "\n\n".join(lines)


class AskUserConfig(BaseToolConfig):
    """Configuration for the ask_user tool."""

    permission: ToolPermission = ToolPermission.ALWAYS
    max_question_length: int = 2000
    max_choices: int = 4
    max_questions: int = 6


class AskUserState(BaseToolState):
    """State for the ask_user tool."""

    questions_asked: int = 0


# Type for the user input callback
UserInputCallback = Callable[
    [AskUserArgs],
    Awaitable[AskUserResult],
]


class AskUserQuestion(
    BaseTool[AskUserArgs, AskUserResult, AskUserConfig, AskUserState],
    ToolUIData[AskUserArgs, AskUserResult],
):
    """Tool for asking the user questions during agent execution."""

    description: ClassVar[str] = (
        "Ask the user one or more multiple-choice questions and wait for their responses. "
        "Supports up to 6 questions at once, displayed as tabs. Each question has 2-6 choices, "
        "plus an automatic 'Other' option for free text input. The first option is recommended by default. "
        "Questions can be single-select (default) or multi-select. "
        "User navigates with arrows, Enter confirms/toggles, Tab switches questions. "
        "For single questions with single-select, auto-submits when user confirms a choice."
    )

    # Class-level callback that will be set by the UI
    _user_input_callback: ClassVar[UserInputCallback | None] = None

    @classmethod
    def set_user_input_callback(cls, callback: UserInputCallback | None) -> None:
        """Set the callback for handling user input requests."""
        cls._user_input_callback = callback

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, AskUserArgs):
            return ToolCallDisplay(summary="Invalid arguments")

        args = event.args
        count = len(args.questions)

        if count == 1:
            q = args.questions[0]
            question_preview = (
                q.question[:50] + "..." if len(q.question) > 50 else q.question  # noqa: PLR2004
            )
            return ToolCallDisplay(
                summary=f"Asking ({len(q.choices)} choices): {question_preview}"
            )
        else:
            return ToolCallDisplay(summary=f"Asking {count} questions")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, AskUserResult):
            if event.error:
                return ToolResultDisplay(success=False, message=event.error)
            return ToolResultDisplay(success=True, message="Questions answered")

        result = event.result

        if not result.answered:
            return ToolResultDisplay(success=False, message="User cancelled")

        if len(result.answers) == 1:
            answer = result.answers[0]
            answer_preview = (
                answer.answer[:50] + "..."
                if len(answer.answer) > 50  # noqa: PLR2004
                else answer.answer
            )
            prefix = "(Other) " if answer.is_other else ""
            return ToolResultDisplay(success=True, message=f"{prefix}{answer_preview}")
        else:
            return ToolResultDisplay(
                success=True, message=f"{len(result.answers)} answers received"
            )

    @classmethod
    def get_status_text(cls) -> str:
        return "Waiting for user input"

    async def run(self, args: AskUserArgs) -> AskUserResult:
        # Validate number of questions
        if len(args.questions) > self.config.max_questions:
            raise ToolError(
                f"Too many questions ({len(args.questions)}). "
                f"Max: {self.config.max_questions}"
            )

        # Validate each question
        for i, q in enumerate(args.questions):
            if len(q.question) > self.config.max_question_length:
                raise ToolError(
                    f"Question {i + 1} too long ({len(q.question)} chars). "
                    f"Max: {self.config.max_question_length}"
                )

            if len(q.choices) < 2:  # noqa: PLR2004
                raise ToolError(
                    f"Question {i + 1}: at least 2 choices required"
                )
            if len(q.choices) > self.config.max_choices:
                raise ToolError(
                    f"Question {i + 1}: too many choices ({len(q.choices)}). "
                    f"Max: {self.config.max_choices}"
                )

        # Check if callback is set (use type(self) to get the dynamically loaded class)
        callback = type(self)._user_input_callback
        if callback is None:
            raise ToolError(
                "User input not available. "
                "This tool requires an interactive UI session."
            )

        # Update state
        self.state.questions_asked += len(args.questions)

        # Call the UI callback and wait for response
        result = await callback(args)

        return result
