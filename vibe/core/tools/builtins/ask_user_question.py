from __future__ import annotations

from typing import ClassVar

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.questions import (
    UserQuestionRequest as AskUserQuestionArgs,
    UserQuestionResult as AskUserQuestionResult,
)


class AskUserQuestionConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class AskUserQuestion(
    BaseTool[
        AskUserQuestionArgs, AskUserQuestionResult, AskUserQuestionConfig, BaseToolState
    ]
):
    description: ClassVar[str] = (
        "Ask the user questions with structured options and wait for their answers."
    )
