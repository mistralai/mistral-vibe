from __future__ import annotations

import pytest

from vibe.core.tools.base import BaseToolState
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestion,
    AskUserQuestionConfig,
)


@pytest.fixture
def tool():
    config = AskUserQuestionConfig()
    return AskUserQuestion(config_getter=lambda: config, state=BaseToolState())
