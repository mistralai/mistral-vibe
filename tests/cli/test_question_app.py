from __future__ import annotations

import pytest

from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    Choice,
    Question,
)


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


@pytest.fixture
def multi_select_args():
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which features?",
                header="Features",
                options=[
                    Choice(label="Auth"),
                    Choice(label="Caching"),
                    Choice(label="Logging"),
                ],
                multi_select=True,
            )
        ]
    )


class TestQuestionAppState:
    def test_init_state(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)

        assert app.current_question_idx == 0
        assert app.selected_option == 0
        assert len(app.answers) == 0
        assert len(app.other_texts) == 0

    def test_total_options_single_select(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)

        # 2 options + Other = 3 (no Submit for single-select)
        assert app._total_options == 3

    def test_total_options_multi_select_includes_submit(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)

        # 3 options + Other + Submit = 5
        assert app._total_options == 5
        assert app._other_option_idx == 3
        assert app._submit_option_idx == 4

    def test_is_other_selected(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)

        assert app._is_other_selected is False
        app.selected_option = 2  # Other option
        assert app._is_other_selected is True

    def test_is_submit_selected(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)

        assert app._is_submit_selected is False
        app.selected_option = 4  # Submit option
        assert app._is_submit_selected is True

    def test_is_submit_selected_false_for_single_select(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)

        # Even if selected_option is 3, is_submit_selected is False for single-select
        app.selected_option = 3
        assert app._is_submit_selected is False

    def test_store_other_text_per_question(self, multi_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_question_args)

        # Store text for question 0
        app.other_texts[0] = "Custom DB"

        # Switch to question 1
        app.current_question_idx = 1
        app.other_texts[1] = "Custom Framework"

        # Verify both stored separately
        assert app._get_other_text(0) == "Custom DB"
        assert app._get_other_text(1) == "Custom Framework"

    def test_save_regular_option_answer(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        app.selected_option = 0  # PostgreSQL

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert answer_text == "PostgreSQL"
        assert is_other is False

    def test_save_other_option_answer(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        app.selected_option = 2  # Other
        app.other_texts[0] = "SQLite"

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert answer_text == "SQLite"
        assert is_other is True

    def test_save_other_option_empty_does_not_save(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        app.selected_option = 2  # Other
        app.other_texts[0] = ""  # Empty

        app._save_current_answer()

        assert 0 not in app.answers

    def test_all_answered_false_initially(self, multi_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_question_args)

        assert app._all_answered() is False

    def test_all_answered_true_when_complete(self, multi_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_question_args)
        app.answers[0] = ("PostgreSQL", False)
        app.answers[1] = ("FastAPI", False)

        assert app._all_answered() is True

    def test_multi_select_toggle(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)

        # Initially no selections
        assert len(app.multi_selections.get(0, set())) == 0

        # Add selection
        app.multi_selections.setdefault(0, set()).add(0)
        assert 0 in app.multi_selections[0]

        # Add another
        app.multi_selections[0].add(2)
        assert 2 in app.multi_selections[0]

        # Remove first
        app.multi_selections[0].discard(0)
        assert 0 not in app.multi_selections[0]
        assert 2 in app.multi_selections[0]

    def test_multi_select_save_answer(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        app.multi_selections[0] = {0, 2}  # Auth and Logging

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert "Auth" in answer_text
        assert "Logging" in answer_text
        assert is_other is False

    def test_multi_select_with_other(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        app.multi_selections[0] = {0, 3}  # Auth and Other
        app.other_texts[0] = "Custom Feature"

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert "Auth" in answer_text
        assert "Custom Feature" in answer_text
        assert is_other is True


class TestQuestionAppActions:
    def test_action_move_down(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        assert app.selected_option == 0

        app.action_move_down()
        assert app.selected_option == 1

        app.action_move_down()
        assert app.selected_option == 2  # Other

        app.action_move_down()
        assert app.selected_option == 0  # Wraps around

    def test_action_move_up(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        assert app.selected_option == 0

        app.action_move_up()
        assert app.selected_option == 2  # Wraps to Other

        app.action_move_up()
        assert app.selected_option == 1

    def test_switch_question_preserves_other_text(self, multi_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_question_args)
        app.other_texts[0] = "Text for Q1"

        app._switch_question(1)

        assert app.current_question_idx == 1
        assert app._get_other_text(0) == "Text for Q1"
        assert app._get_other_text(1) == ""


class TestMultiSelectOtherBehavior:
    def test_multi_select_other_does_not_advance_on_save(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        app.selected_option = 3  # Other option (3 options + Other)
        app.other_texts[0] = "Custom feature"

        # Save should not advance for multi-select
        app._save_current_answer()

        # Should stay on same question
        assert app.current_question_idx == 0

    def test_multi_select_other_toggle_adds_to_selections(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        other_idx = len(app._current_question.options)  # 3

        # Initially no selections
        assert len(app.multi_selections.get(0, set())) == 0

        # Add Other to selections
        app.multi_selections.setdefault(0, set()).add(other_idx)
        app.other_texts[0] = "Custom"

        # Can still add regular options
        app.multi_selections[0].add(0)  # Auth
        app.multi_selections[0].add(1)  # Caching

        assert other_idx in app.multi_selections[0]
        assert 0 in app.multi_selections[0]
        assert 1 in app.multi_selections[0]

    def test_multi_select_save_with_other_and_regular_options(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        other_idx = len(app._current_question.options)

        # Select Auth (0), Logging (2), and Other (3)
        app.multi_selections[0] = {0, 2, other_idx}
        app.other_texts[0] = "Custom Feature"

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert "Auth" in answer_text
        assert "Logging" in answer_text
        assert "Custom Feature" in answer_text
        assert is_other is True

    def test_multi_select_other_without_text_not_in_answer(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        other_idx = len(app._current_question.options)

        # Select Auth (0) and Other (3) but no text for Other
        app.multi_selections[0] = {0, other_idx}
        app.other_texts[0] = ""

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert "Auth" in answer_text
        # Empty Other should not appear
        assert is_other is False  # No valid Other text

    def test_multi_select_can_toggle_after_selecting_other(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        other_idx = len(app._current_question.options)

        # Select Other first
        app.multi_selections[0] = {other_idx}
        app.other_texts[0] = "Custom"

        # Now navigate to and toggle Auth
        app.selected_option = 0
        selections = app.multi_selections.setdefault(0, set())
        selections.add(0)  # Toggle Auth on

        assert 0 in app.multi_selections[0]
        assert other_idx in app.multi_selections[0]

        # Toggle Auth off
        selections.discard(0)
        assert 0 not in app.multi_selections[0]
        assert other_idx in app.multi_selections[0]

    def test_multi_select_empty_selections_does_not_save(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)

        # No selections
        app._save_current_answer()

        assert 0 not in app.answers


class TestSingleSelectOtherBehavior:
    def test_single_select_other_with_text_saves(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        app.selected_option = 2  # Other
        app.other_texts[0] = "Custom DB"

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert answer_text == "Custom DB"
        assert is_other is True

    def test_single_select_other_without_text_does_not_save(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        app.selected_option = 2  # Other
        app.other_texts[0] = ""

        app._save_current_answer()

        assert 0 not in app.answers

    def test_single_select_regular_option_saves_immediately(self, single_question_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(single_question_args)
        app.selected_option = 1  # MongoDB

        app._save_current_answer()

        assert 0 in app.answers
        answer_text, is_other = app.answers[0]
        assert answer_text == "MongoDB"
        assert is_other is False


class TestMultiSelectAutoSelect:
    def test_typing_auto_selects_other(self, multi_select_args):
        from unittest.mock import MagicMock

        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        app.other_input = MagicMock()
        app.other_input.value = "Custom text"

        # Initially no selections
        assert app._other_option_idx not in app.multi_selections.get(0, set())

        # Simulate input change
        from textual.widgets import Input

        app.on_input_changed(Input.Changed(app.other_input, "Custom text"))

        # Other should now be selected
        assert app._other_option_idx in app.multi_selections[0]

    def test_clearing_auto_deselects_other(self, multi_select_args):
        from unittest.mock import MagicMock

        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        app.other_input = MagicMock()

        # Start with Other selected and text
        app.multi_selections[0] = {app._other_option_idx}
        app.other_input.value = ""  # Cleared

        # Simulate input change with empty value
        from textual.widgets import Input

        app.on_input_changed(Input.Changed(app.other_input, ""))

        # Other should now be deselected
        assert app._other_option_idx not in app.multi_selections[0]

    def test_auto_select_preserves_other_selections(self, multi_select_args):
        from unittest.mock import MagicMock

        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        app.other_input = MagicMock()
        app.other_input.value = "Custom"

        # Pre-select Auth and Logging
        app.multi_selections[0] = {0, 2}

        # Simulate typing
        from textual.widgets import Input

        app.on_input_changed(Input.Changed(app.other_input, "Custom"))

        # All selections should be preserved plus Other
        assert 0 in app.multi_selections[0]
        assert 2 in app.multi_selections[0]
        assert app._other_option_idx in app.multi_selections[0]


class TestMultiSelectSubmit:
    def test_navigate_to_submit(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)

        # Navigate down through all options to Submit
        for _ in range(4):  # 0->1->2->3(Other)->4(Submit)
            app.action_move_down()

        assert app.selected_option == 4
        assert app._is_submit_selected is True

    def test_submit_wraps_around(self, multi_select_args):
        from vibe.cli.textual_ui.widgets.question_app import QuestionApp

        app = QuestionApp(multi_select_args)
        app.selected_option = 4  # Submit

        app.action_move_down()

        assert app.selected_option == 0
