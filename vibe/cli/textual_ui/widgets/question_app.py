from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.widgets import Input, Static

if TYPE_CHECKING:
    from vibe.core.tools.builtins.ask_user_question import AskUserArgs, Question


class EscapableInput(Input):
    """Input that posts escape events to parent."""

    class EscapePressed(Message):
        """Escape was pressed in the input."""

        pass

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.post_message(self.EscapePressed())


class QuestionTab(Static):
    """A single tab in the tab bar."""

    def __init__(self, label: str, index: int, is_active: bool = False) -> None:
        super().__init__(classes="question-tab")
        self.tab_label = label
        self.tab_index = index
        self.is_active = is_active
        self.is_answered = False

    def on_mount(self) -> None:
        self._update_display()

    def set_active(self, active: bool) -> None:
        self.is_active = active
        self._update_display()

    def set_answered(self, answered: bool) -> None:
        self.is_answered = answered
        self._update_display()

    def _update_display(self) -> None:
        check = " ✓" if self.is_answered else ""
        self.update(f"{self.tab_label}{check}")
        if self.is_active:
            self.add_class("question-tab-active")
        else:
            self.remove_class("question-tab-active")
        if self.is_answered:
            self.add_class("question-tab-answered")
        else:
            self.remove_class("question-tab-answered")


class QuestionPanel(Container):
    """Panel showing a single question's choices."""

    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    class CancelRequested(Message):
        """Bubble up cancel request to parent."""

        pass

    def action_cancel(self) -> None:
        self.post_message(self.CancelRequested())

    def on_escapable_input_escape_pressed(
        self, message: EscapableInput.EscapePressed
    ) -> None:
        """Handle escape from input."""
        self.post_message(self.CancelRequested())

    def on_input_changed(self, event: Input.Changed) -> None:
        """Auto-confirm 'Other' option when user starts typing (single-select only)."""
        if not self.multi_select and event.value:
            # User started typing in "Other" field, auto-confirm it
            other_idx = len(self.choices)
            if self.confirmed_option != other_idx:
                self.confirmed_option = other_idx
                self._update_options()

    def on_key(self, event: events.Key) -> None:
        """Handle escape when panel is focused."""
        if event.key == "escape":
            event.stop()
            self.post_message(self.CancelRequested())

    def __init__(self, question: Question, index: int, max_choices: int = 6) -> None:
        super().__init__(classes="question-panel")
        self.question_data = question
        self.index = index
        self.choices = question.choices or []
        self.max_choices = max_choices
        self.multi_select = getattr(question, "multi_select", False)
        self.recommended_index = getattr(question, "recommended_index", None)

        # Selection state
        self.selected_option = 0  # Currently focused option (cursor position)
        self.total_options = len(self.choices) + 1  # +1 for "Other"
        self.option_widgets: list[Static] = []

        # Multi-select: set of checked option indices
        self.checked_options: set[int] = set()

        # Single-select: confirmed option index (None = not yet confirmed)
        self.confirmed_option: int | None = None

        # Text input for "Other" option
        self.text_input: Input | None = None
        self.other_cursor: Static | None = None
        self.other_check: Static | None = None  # ✓ marker for single-select
        self.is_other_mode = False
        self.other_checked = False  # For multi-select "Other"

    def compose(self) -> ComposeResult:
        # Question text (will be highlighted when active)
        yield Static(self.question_data.question, classes="question-text")

        # Choice options
        for _ in range(len(self.choices)):
            widget = Static("", classes="question-option")
            self.option_widgets.append(widget)
            yield widget

        # "Other" option as inline input with cursor prefix
        with Horizontal(classes="question-other-line"):
            other_num = len(self.choices) + 1
            # Initial cursor state (not selected)
            self.other_cursor = Static(f"   {other_num}. ", classes="question-other-cursor")
            yield self.other_cursor
            self.text_input = EscapableInput(
                placeholder="Other (type your answer)",
                classes="question-other-input",
            )
            yield self.text_input
            # Check marker (for single-select confirmation)
            self.other_check = Static("", classes="question-other-check")
            yield self.other_check

        # Dynamic padding: fill remaining space based on max choices
        padding_lines = self.max_choices - len(self.choices)
        if padding_lines > 0:
            yield Static("\n" * (padding_lines - 1), classes="question-padding")

    async def on_mount(self) -> None:
        self._update_options()

    def _update_options(self) -> None:  # noqa: PLR0912, PLR0915
        for idx, widget in enumerate(self.option_widgets):
            is_focused = idx == self.selected_option
            is_confirmed = self.confirmed_option == idx
            num = idx + 1

            choice = self.choices[idx]
            text = choice.label
            # Add "(Recommended)" indicator if specified
            if self.recommended_index is not None and idx == self.recommended_index:
                text += " (Recommended)"
            if choice.description:
                text += f" - {choice.description}"

            if self.multi_select:
                # Multi-select: show checkbox
                is_checked = idx in self.checked_options
                checkbox = "[x]" if is_checked else "[ ]"
                cursor = " > " if is_focused else "   "
                widget.update(f"{cursor}{num}. {checkbox} {text}")
            else:
                # Single-select: show cursor and confirmation marker
                marker = " ✓" if is_confirmed else ""
                cursor = " > " if is_focused else "   "
                widget.update(f"{cursor}{num}. {text}{marker}")

            # Update CSS classes
            widget.remove_class("question-option-selected")
            widget.remove_class("question-option-confirmed")
            if is_focused:
                widget.add_class("question-option-selected")
            if is_confirmed and not self.multi_select:
                widget.add_class("question-option-confirmed")

        # Update "Other" cursor and input
        is_other_focused = self.selected_option == len(self.choices)
        is_other_confirmed = self.confirmed_option == len(self.choices)
        was_other_mode = self.is_other_mode
        other_num = len(self.choices) + 1

        if self.other_cursor:
            if self.multi_select:
                checkbox = "[x]" if self.other_checked else "[ ]"
                cursor = " > " if is_other_focused else "   "
                self.other_cursor.update(f"{cursor}{other_num}. {checkbox} ")
            else:
                cursor = " > " if is_other_focused else "   "
                self.other_cursor.update(f"{cursor}{other_num}. ")

            # Update cursor styling for confirmed state
            self.other_cursor.remove_class("question-other-confirmed")
            if is_other_confirmed and not self.multi_select:
                self.other_cursor.add_class("question-other-confirmed")

        if self.other_check:
            # Show ✓ marker for confirmed "Other" in single-select
            if is_other_confirmed and not self.multi_select:
                self.other_check.update(" ✓")
                self.other_check.add_class("question-other-confirmed")
            else:
                self.other_check.update("")
                self.other_check.remove_class("question-other-confirmed")

        if self.text_input:
            self.is_other_mode = is_other_focused

            # Update input styling for confirmed state
            self.text_input.remove_class("question-other-confirmed")
            if is_other_confirmed and not self.multi_select:
                self.text_input.add_class("question-other-confirmed")

            if is_other_focused:
                self.text_input.focus()
            elif was_other_mode:
                self.focus()

    def move_up(self) -> None:
        self.selected_option = (self.selected_option - 1) % self.total_options
        self._update_options()

    def move_down(self) -> None:
        self.selected_option = (self.selected_option + 1) % self.total_options
        self._update_options()

    def toggle_current(self) -> None:
        """Toggle the currently focused option (for multi-select mode)."""
        if not self.multi_select:
            return

        if self.selected_option < len(self.choices):
            # Toggle regular option
            if self.selected_option in self.checked_options:
                self.checked_options.discard(self.selected_option)
            else:
                self.checked_options.add(self.selected_option)
        else:
            # Toggle "Other" option
            self.other_checked = not self.other_checked

        self._update_options()

    def confirm_current(self) -> None:
        """Confirm the currently focused option (for single-select mode)."""
        if self.multi_select:
            return
        self.confirmed_option = self.selected_option
        self._update_options()

    def is_answered(self) -> bool:
        """Check if this question has been answered."""
        if self.multi_select:
            # Multi-select: at least one option checked, or "Other" checked WITH content
            has_regular_selection = len(self.checked_options) > 0
            has_other_with_content = (
                self.other_checked
                and self.text_input
                and self.text_input.value.strip()
            )
            return has_regular_selection or has_other_with_content
        else:
            # Single-select: must have confirmed option
            # If "Other" is confirmed, must have content
            if self.confirmed_option is None:
                return False
            if self.confirmed_option == len(self.choices):
                # "Other" is confirmed - check it has content
                return bool(self.text_input and self.text_input.value.strip())
            return True

    def get_answer(self) -> tuple[str, bool]:
        """Get the current answer and whether it's an 'Other' response."""
        if self.multi_select:
            # Multi-select: return comma-separated list of selected labels
            selected_labels = [
                self.choices[idx].label
                for idx in sorted(self.checked_options)
            ]

            # Add "Other" if checked and has content
            other_text = ""
            if self.other_checked and self.text_input:
                other_text = self.text_input.value.strip()
                if other_text:
                    selected_labels.append(other_text)

            has_other = self.other_checked and bool(other_text)
            return ", ".join(selected_labels), has_other
        else:
            # Single-select: return the confirmed option (or selected if not confirmed)
            answer_idx = self.confirmed_option if self.confirmed_option is not None else self.selected_option
            if answer_idx < len(self.choices):
                return self.choices[answer_idx].label, False
            else:
                if self.text_input:
                    return self.text_input.value.strip(), True
                return "", True

    def focus_input(self) -> None:
        """Focus the appropriate input for this question."""
        if self.is_other_mode and self.text_input:
            self.text_input.focus()
        else:
            self.focus()


class RecapPanel(Container):
    """Panel showing a summary of all answers before final submission."""

    can_focus = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("enter", "submit", "Submit", show=False),
    ]

    class SubmitRequested(Message):
        """User clicked the submit button."""

        pass

    class CancelRequested(Message):
        """User wants to cancel."""

        pass

    def action_cancel(self) -> None:
        self.post_message(self.CancelRequested())

    def action_submit(self) -> None:
        self.post_message(self.SubmitRequested())

    def __init__(self, answers: list[tuple[str, str, bool]]) -> None:
        super().__init__(classes="recap-panel")
        self.answers = answers
        self.submit_widget: Static | None = None

    def compose(self) -> ComposeResult:
        yield Static("Summary", classes="recap-title")

        for i, (question, answer, is_other) in enumerate(self.answers):
            q_preview = question[:60] + "..." if len(question) > 60 else question  # noqa: PLR2004
            prefix = "(custom) " if is_other else ""
            yield Static(
                f"  {i + 1}. {q_preview}\n     → {prefix}{answer}",
                classes="recap-answer",
            )

        # Submit button (always selected)
        self.submit_widget = Static(
            "  [ Submit ]", classes="recap-submit recap-item-selected"
        )
        yield self.submit_widget

    def select_current(self) -> None:
        """Submit is always selected, so just post the message."""
        self.post_message(self.SubmitRequested())


class QuestionApp(Container):
    """Widget for asking the user one or more questions with tabs."""

    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("left", "prev_question", "Previous Question", show=False),
        Binding("right", "next_question", "Next Question", show=False),
        Binding("tab", "tab_forward", "Next/Validate", show=False),
        Binding("shift+tab", "prev_question", "Previous Question", show=False),
        Binding("enter", "submit", "Submit", show=False),
        Binding("space", "toggle", "Toggle", show=False),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    class Answered(Message):
        """Message sent when user answers all questions."""

        def __init__(self, answers: list[tuple[str, str, bool]]) -> None:
            super().__init__()
            # List of (question, answer, is_other)
            self.answers = answers

    class Cancelled(Message, bubble=True):
        """Message sent when user cancels."""

        pass

    def __init__(self, args: AskUserArgs) -> None:
        super().__init__(id="question-app")
        self.args = args
        self.questions = args.questions
        self.current_tab = 0
        self.tab_widgets: list[QuestionTab] = []
        self.validate_tab: QuestionTab | None = None
        self.panel_widgets: list[QuestionPanel] = []
        self.in_recap_mode = False
        self.recap_panel: RecapPanel | None = None
        self.help_widget: Static | None = None
        # Calculate max choices across all questions for consistent padding
        self.max_choices = max(len(q.choices) for q in self.questions) if self.questions else 6

    def compose(self) -> ComposeResult:
        with Vertical(id="question-content"):
            # Tab bar
            with Horizontal(classes="question-tabs"):
                for i, q in enumerate(self.questions):
                    header = getattr(q, "header", "")
                    label = f"Q{i + 1}: {header}" if header else f"Q{i + 1}"
                    tab = QuestionTab(label, i, is_active=(i == 0))
                    self.tab_widgets.append(tab)
                    yield tab

                # Submit tab (hidden until all questions answered)
                self.validate_tab = QuestionTab("Submit", len(self.questions), is_active=False)
                self.validate_tab.add_class("question-tab-validate")
                self.validate_tab.display = False
                yield self.validate_tab

            # Question panels (only one visible at a time)
            with Container(classes="question-panels-container"):
                for i, q in enumerate(self.questions):
                    panel = QuestionPanel(q, i, max_choices=self.max_choices)
                    panel.display = (i == 0)  # Only first visible
                    self.panel_widgets.append(panel)
                    yield panel

            # Help text
            self.help_widget = Static(self._get_help_text(), classes="question-help")
            yield self.help_widget

    def _get_help_text(self) -> str:
        if self.in_recap_mode:
            return "Enter: submit  |  Escape: cancel"

        # Check if current question is multi-select
        panel = self._get_current_panel()
        is_multi = panel and panel.multi_select

        if is_multi:
            if len(self.questions) > 1:
                return "↑↓: navigate  |  ←→: questions  |  Enter/Space: toggle  |  Tab: next  |  Esc: cancel"
            else:
                return "↑↓: navigate  |  Enter/Space: toggle  |  Tab: submit  |  Escape: cancel"
        elif len(self.questions) > 1:
            return "↑↓: select  |  ←→: questions  |  Enter: confirm  |  Tab: next  |  Esc: cancel"
        else:
            # Single question single-select: Enter confirms AND submits (auto-submit)
            return "↑↓: select  |  Enter: confirm & submit  |  Escape: cancel"

    def _update_help_text(self) -> None:
        if self.help_widget:
            self.help_widget.update(self._get_help_text())

    async def on_mount(self) -> None:
        # Focus the first panel and highlight its question
        if self.panel_widgets:
            self.panel_widgets[0].query_one(".question-text").add_class("question-text-active")
            self.call_after_refresh(self.panel_widgets[0].focus_input)

    def _get_current_panel(self) -> QuestionPanel | None:
        if 0 <= self.current_tab < len(self.panel_widgets):
            return self.panel_widgets[self.current_tab]
        return None

    def _switch_tab(self, new_tab: int) -> None:
        if self.in_recap_mode or new_tab == self.current_tab:
            return

        # Update tab appearance
        if self.tab_widgets:
            self.tab_widgets[self.current_tab].set_active(False)
            self.tab_widgets[new_tab].set_active(True)

        # Update question text highlight
        old_panel = self.panel_widgets[self.current_tab]
        new_panel = self.panel_widgets[new_tab]
        old_panel.query_one(".question-text").remove_class("question-text-active")
        new_panel.query_one(".question-text").add_class("question-text-active")

        # Hide old panel, show new panel
        old_panel.display = False
        new_panel.display = True

        self.current_tab = new_tab

        # Focus the new panel
        new_panel.focus_input()

        # Update help text (may differ between single/multi-select questions)
        self._update_help_text()

    def action_move_up(self) -> None:
        if self.in_recap_mode:
            # No navigation in recap mode
            return
        panel = self._get_current_panel()
        if panel:
            panel.move_up()

    def action_move_down(self) -> None:
        if self.in_recap_mode:
            # No navigation in recap mode
            return
        panel = self._get_current_panel()
        if panel:
            panel.move_down()

    def action_tab_forward(self) -> None:
        """Tab key: go to next question or show recap if on last question."""
        if self.in_recap_mode:
            return

        # If on last question (or single question), Tab goes to recap
        if self.current_tab >= len(self.panel_widgets) - 1:
            self.run_worker(self._show_recap())
        elif len(self.panel_widgets) > 1:
            self._switch_tab(self.current_tab + 1)

    def action_next_question(self) -> None:
        """Right arrow: go to next question, or to Valider if all answered."""
        if self.in_recap_mode:
            return
        # If on last question and all answered, go to recap
        if self.current_tab >= len(self.panel_widgets) - 1:
            all_answered = all(panel.is_answered() for panel in self.panel_widgets)
            if all_answered:
                self.run_worker(self._show_recap())
        elif len(self.panel_widgets) > 1:
            self._switch_tab(self.current_tab + 1)

    def action_prev_question(self) -> None:
        """Left arrow / Shift+Tab: go to previous question (no wrap)."""
        if self.in_recap_mode:
            return
        # Only navigate if multiple questions and not on first
        if len(self.panel_widgets) > 1 and self.current_tab > 0:
            self._switch_tab(self.current_tab - 1)

    def _update_current_tab_status(self) -> None:
        """Update the current tab's answered status."""
        if self.tab_widgets and 0 <= self.current_tab < len(self.tab_widgets):
            panel = self._get_current_panel()
            if panel:
                self.tab_widgets[self.current_tab].set_answered(panel.is_answered())
        self._update_validate_tab_visibility()

    def _update_validate_tab_visibility(self) -> None:
        """Show/hide the Valider tab based on whether all questions are answered."""
        if not self.validate_tab:
            return
        all_answered = all(panel.is_answered() for panel in self.panel_widgets)
        self.validate_tab.display = all_answered

    def action_toggle(self) -> None:
        """Toggle the current option (for multi-select questions)."""
        if self.in_recap_mode:
            return
        panel = self._get_current_panel()
        if panel and panel.multi_select:
            panel.toggle_current()
            self._update_current_tab_status()

    def action_submit(self) -> None:
        if self.in_recap_mode:
            # In recap mode, Enter selects the current item (edit question or submit)
            if self.recap_panel:
                self.recap_panel.select_current()
            return

        panel = self._get_current_panel()
        if not panel:
            return

        if panel.multi_select:
            # Multi-select: Enter toggles the current option
            panel.toggle_current()
        else:
            # Single-select: Enter confirms the current choice
            panel.confirm_current()

        self._update_current_tab_status()

        # After confirming a single-select answer, auto-advance
        if not panel.multi_select and panel.is_answered():
            all_answered = all(p.is_answered() for p in self.panel_widgets)
            if all_answered:
                # All questions answered: go to recap (or submit if single question)
                if len(self.questions) == 1:
                    self._do_submit()
                else:
                    self.run_worker(self._show_recap())
            elif self.current_tab < len(self.panel_widgets) - 1:
                # Not all answered: go to next question
                self._switch_tab(self.current_tab + 1)

    def action_cancel(self) -> None:
        # Escape always cancels the entire questionnaire
        self.post_message(self.Cancelled())

    def _get_answers(self) -> list[tuple[str, str, bool]]:
        answers = []
        for panel in self.panel_widgets:
            answer, is_other = panel.get_answer()
            answers.append((panel.question_data.question, answer, is_other))
        return answers

    async def _show_recap(self) -> None:
        self.in_recap_mode = True

        # Hide all question panels
        for panel in self.panel_widgets:
            panel.display = False

        # Deactivate current question tab, activate Valider tab
        if self.tab_widgets and 0 <= self.current_tab < len(self.tab_widgets):
            self.tab_widgets[self.current_tab].set_active(False)
        if self.validate_tab:
            self.validate_tab.set_active(True)

        # Create and show recap panel
        answers = self._get_answers()
        panels_container = self.query_one(".question-panels-container")
        self.recap_panel = RecapPanel(answers)
        await panels_container.mount(self.recap_panel)

        self._update_help_text()
        self.recap_panel.focus()

    def _do_submit(self) -> None:
        answers = self._get_answers()
        self.post_message(self.Answered(answers=answers))

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle text changes in Other field - update tab status."""
        self._update_current_tab_status()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in text input (Other field)."""
        panel = self._get_current_panel()
        if not panel:
            return

        if panel.multi_select:
            # In multi-select, Enter in "Other" field toggles the Other checkbox
            panel.toggle_current()
        else:
            # In single-select, Enter in "Other" field confirms the choice
            panel.confirm_current()

        self._update_current_tab_status()

        # Auto-submit: if single question with single-select and answered, submit directly
        if (
            len(self.questions) == 1
            and not panel.multi_select
            and panel.is_answered()
        ):
            self._do_submit()

    def on_question_panel_cancel_requested(
        self, message: QuestionPanel.CancelRequested
    ) -> None:
        """Handle cancel request from panel - always cancel entirely."""
        self.post_message(self.Cancelled())

    def on_recap_panel_cancel_requested(
        self, message: RecapPanel.CancelRequested
    ) -> None:
        """Handle cancel request from recap panel."""
        self.post_message(self.Cancelled())

    def on_recap_panel_submit_requested(
        self, message: RecapPanel.SubmitRequested
    ) -> None:
        """Handle final submission from recap panel."""
        self._do_submit()
