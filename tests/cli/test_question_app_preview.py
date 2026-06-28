from __future__ import annotations

from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    Choice,
    Question,
)


class _FakeStatic:
    def __init__(self) -> None:
        self.content = ""
        self.display = True

    def update(self, text: str) -> None:
        self.content = text


def _args_with_preview() -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which layout?",
                header="Layout",
                options=[
                    Choice(label="Grid", preview="┌─┬─┐\n│ │ │\n└─┴─┘"),
                    Choice(label="List"),
                ],
            )
        ]
    )


def test_choice_accepts_preview_field():
    choice = Choice(label="X", preview="hello world")
    assert choice.preview == "hello world"


def test_choice_preview_defaults_empty():
    assert Choice(label="X").preview == ""


def test_preview_shown_for_focused_option_with_preview():
    app = QuestionApp(_args_with_preview())
    app.preview_widget = _FakeStatic()  # type: ignore[assignment]

    app.selected_option = 0
    app._update_preview()

    assert app.preview_widget.display is True
    assert "┌─┬─┐" in app.preview_widget.content


def test_preview_hidden_for_option_without_preview():
    app = QuestionApp(_args_with_preview())
    app.preview_widget = _FakeStatic()  # type: ignore[assignment]

    app.selected_option = 1
    app._update_preview()

    assert app.preview_widget.display is False
    assert app.preview_widget.content == ""


def test_preview_hidden_when_other_option_focused():
    app = QuestionApp(_args_with_preview())
    app.preview_widget = _FakeStatic()  # type: ignore[assignment]

    app.selected_option = app._other_option_idx
    app._update_preview()

    assert app.preview_widget.display is False
