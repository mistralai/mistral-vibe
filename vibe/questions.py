from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

__all__ = [
    "QuestionChoice",
    "UserAnswer",
    "UserQuestion",
    "UserQuestionRequest",
    "UserQuestionResult",
]


class QuestionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, extra="forbid", populate_by_name=True
    )


class QuestionChoice(QuestionModel):
    label: str = Field(description="Short label for the choice (1-5 words)")
    description: str = Field(
        default="", description="Optional explanation of this choice"
    )


class UserQuestion(QuestionModel):
    question: str = Field(description="The question text")
    header: str = Field(
        default="",
        description="Short chip/tag label for the question (max 20 chars)",
        max_length=20,
    )
    options: list[QuestionChoice] = Field(
        description="Available options (2-4 recommended, not including 'Other'). An 'Other' option for free text is automatically added.",
        min_length=2,
    )
    multi_select: bool = Field(
        default=False, description="If true, user can select multiple options"
    )
    hide_other: bool = Field(
        default=False, description="If true, hide the 'Other' free text option"
    )


class UserQuestionRequest(QuestionModel):
    questions: list[UserQuestion] = Field(
        description="Questions to ask (1-4 recommended). Displayed as tabs if multiple.",
        min_length=1,
    )
    footer_note: str | None = Field(
        default=None,
        description="Optional subtle note displayed at the bottom of the question widget.",
    )


class UserAnswer(QuestionModel):
    question: str = Field(description="The original question")
    answer: str = Field(description="The user's answer")
    is_other: bool = Field(
        default=False, description="True if user typed a custom answer via 'Other'"
    )


class UserQuestionResult(QuestionModel):
    answers: list[UserAnswer] = Field(description="List of answers")
    cancelled: bool = Field(
        default=False,
        description="True if the user cancelled without answering the questions.",
    )
