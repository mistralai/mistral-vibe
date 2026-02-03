from __future__ import annotations

import pytest

from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.ask_user_question import (
    Answer,
    AskUserQuestion,
    AskUserQuestionArgs,
    AskUserQuestionConfig,
    AskUserQuestionResult,
    Choice,
    Question,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


@pytest.fixture
def tool():
    config = AskUserQuestionConfig()
    return AskUserQuestion(config=config, state=BaseToolState())


@pytest.fixture
def single_question_args():
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which database?",
                header="DB",
                options=[
                    Choice(label="PostgreSQL", description="Relational DB"),
                    Choice(label="MongoDB", description="Document DB"),
                ],
            )
        ]
    )


@pytest.fixture
def multi_question_args():
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which database?",
                header="DB",
                options=[Choice(label="PostgreSQL"), Choice(label="MongoDB")],
            ),
            Question(
                question="Which framework?",
                header="Framework",
                options=[Choice(label="FastAPI"), Choice(label="Django")],
            ),
        ]
    )


async def run_tool_with_callback(tool, args, callback):
    from vibe.core.tools.base import InvokeContext

    ctx = InvokeContext(user_input_callback=callback, tool_call_id="123")
    result = None
    async for item in tool.run(args, ctx):
        result = item
    return result


@pytest.mark.asyncio
async def test_raises_error_without_callback(tool, single_question_args):
    with pytest.raises(ToolError) as err:
        async for _ in tool.run(single_question_args, ctx=None):
            pass

    assert "interactive UI" in str(err.value)


@pytest.mark.asyncio
async def test_calls_callback_and_returns_result(tool, single_question_args):
    expected_result = AskUserQuestionResult(
        answers=[
            Answer(question="Which database?", answer="PostgreSQL", is_other=False)
        ],
        cancelled=False,
    )

    async def mock_callback(args):
        assert args == single_question_args
        return expected_result

    result = await run_tool_with_callback(tool, single_question_args, mock_callback)

    assert result is not None
    assert result == expected_result
    assert result.answers[0].answer == "PostgreSQL"


@pytest.mark.asyncio
async def test_handles_cancelled_result(tool, single_question_args):
    expected_result = AskUserQuestionResult(answers=[], cancelled=True)

    async def mock_callback(args):
        return expected_result

    result = await run_tool_with_callback(tool, single_question_args, mock_callback)

    assert result is not None
    assert result.cancelled is True
    assert len(result.answers) == 0


@pytest.mark.asyncio
async def test_handles_other_response(tool, single_question_args):
    expected_result = AskUserQuestionResult(
        answers=[Answer(question="Which database?", answer="SQLite", is_other=True)],
        cancelled=False,
    )

    async def mock_callback(args):
        return expected_result

    result = await run_tool_with_callback(tool, single_question_args, mock_callback)

    assert result is not None
    assert result.answers[0].is_other is True
    assert result.answers[0].answer == "SQLite"


class TestToolUIDisplay:
    def test_get_call_display_single_question(self, single_question_args):
        event = ToolCallEvent(
            tool_name="ask_user_question",
            tool_class=AskUserQuestion,
            args=single_question_args,
            tool_call_id="123",
        )
        display = AskUserQuestion.get_call_display(event)
        assert "Which database?" in display.summary

    def test_get_call_display_multiple_questions(self, multi_question_args):
        event = ToolCallEvent(
            tool_name="ask_user_question",
            tool_class=AskUserQuestion,
            args=multi_question_args,
            tool_call_id="123",
        )
        display = AskUserQuestion.get_call_display(event)
        assert "2 questions" in display.summary

    def test_get_result_display_success(self):
        result = AskUserQuestionResult(
            answers=[Answer(question="Q?", answer="A", is_other=False)], cancelled=False
        )
        event = ToolResultEvent(
            tool_name="ask_user_question",
            tool_class=AskUserQuestion,
            result=result,
            tool_call_id="123",
        )
        display = AskUserQuestion.get_result_display(event)
        assert display.success is True
        assert "A" in display.message

    def test_get_result_display_cancelled(self):
        result = AskUserQuestionResult(answers=[], cancelled=True)
        event = ToolResultEvent(
            tool_name="ask_user_question",
            tool_class=AskUserQuestion,
            result=result,
            tool_call_id="123",
        )
        display = AskUserQuestion.get_result_display(event)
        assert display.success is False
        assert "cancelled" in display.message.lower()

    def test_get_result_display_other(self):
        result = AskUserQuestionResult(
            answers=[Answer(question="Q?", answer="Custom", is_other=True)],
            cancelled=False,
        )
        event = ToolResultEvent(
            tool_name="ask_user_question",
            tool_class=AskUserQuestion,
            result=result,
            tool_call_id="123",
        )
        display = AskUserQuestion.get_result_display(event)
        assert display.success is True
        assert "(Other)" in display.message
