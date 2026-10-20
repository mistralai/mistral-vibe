from __future__ import annotations

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
    pass
