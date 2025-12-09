from __future__ import annotations

from typing import NamedTuple

from textual import events

from vibe.cli.autocompletion.base import CompletionResult, CompletionView
from vibe.cli.autocompletion.slash_command import SlashCommandController
from vibe.core.autocompletion.completers import CommandCompleter


class Suggestion(NamedTuple):
    alias: str
    description: str


class SuggestionEvent(NamedTuple):
    suggestions: list[Suggestion]
    selected_index: int


class Replacement(NamedTuple):
    start: int
    end: int
    replacement: str


class StubView(CompletionView):
    def __init__(self) -> None:
        self.suggestion_events: list[SuggestionEvent] = []
        self.reset_count = 0
        self.replacements: list[Replacement] = []

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        typed = [Suggestion(alias, description) for alias, description in suggestions]
        self.suggestion_events.append(SuggestionEvent(typed, selected_index))

    def clear_completion_suggestions(self) -> None:
        self.reset_count += 1

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        self.replacements.append(Replacement(start, end, replacement))


def key_event(key: str) -> events.Key:
    return events.Key(key, character=None)


def make_controller(
    *, prefix: str | None = None
) -> tuple[SlashCommandController, StubView]:
    commands = [
        ("/config", "Show current configuration"),
        ("/compact", "Compact history"),
        ("/help", "Display help"),
        ("/config", "Override description"),
        ("/summarize", "Summarize history"),
        ("/logpath", "Show log path"),
        ("/exit", "Exit application"),
        ("/vim", "Toggle vim keybindings"),
    ]
    completer = CommandCompleter(commands)
    view = StubView()
    controller = SlashCommandController(completer, view)

    if prefix is not None:
        controller.on_text_changed(prefix, cursor_index=len(prefix))
        view.suggestion_events.clear()

    return controller, view


def test_on_text_change_emits_matching_suggestions_in_insertion_order_and_ignores_duplicates() -> (
    None
):
    controller, view = make_controller(prefix="/c")

    controller.on_text_changed("/c", cursor_index=2)

    suggestions, selected = view.suggestion_events[-1]
    assert suggestions == [
        Suggestion("/config", "Override description"),
        Suggestion("/compact", "Compact history"),
    ]
    assert selected == 0


def test_on_text_change_filters_suggestions_case_insensitively() -> None:
    controller, view = make_controller(prefix="/c")

    controller.on_text_changed("/CO", cursor_index=3)

    suggestions, _ = view.suggestion_events[-1]
    assert [suggestion.alias for suggestion in suggestions] == ["/config", "/compact"]


def test_on_text_change_clears_suggestions_when_no_matches() -> None:
    controller, view = make_controller(prefix="/c")

    controller.on_text_changed("/c", cursor_index=2)
    controller.on_text_changed("config", cursor_index=6)

    assert view.reset_count >= 1


def test_on_text_change_limits_the_number_of_results_to_five_and_preserve_insertion_order() -> (
    None
):
    controller, view = make_controller(prefix="/")

    controller.on_text_changed("/", cursor_index=1)

    suggestions, selected_index = view.suggestion_events[-1]
    assert len(suggestions) == 5
    assert [suggestion.alias for suggestion in suggestions] == [
        "/config",
        "/compact",
        "/help",
        "/summarize",
        "/logpath",
    ]


def test_on_key_tab_applies_selected_completion() -> None:
    controller, view = make_controller(prefix="/c")

    result = controller.on_key(key_event("tab"), text="/c", cursor_index=2)

    assert result is CompletionResult.HANDLED
    assert view.replacements == [Replacement(0, 2, "/config")]
    assert view.reset_count == 1


def test_on_key_down_and_up_cycle_selection() -> None:
    controller, view = make_controller(prefix="/c")

    controller.on_key(key_event("down"), text="/c", cursor_index=2)
    suggestions, selected_index = view.suggestion_events[-1]
    assert selected_index == 1

    controller.on_key(key_event("down"), text="/c", cursor_index=2)
    suggestions, selected_index = view.suggestion_events[-1]
    assert selected_index == 0

    controller.on_key(key_event("up"), text="/c", cursor_index=2)
    suggestions, selected_index = view.suggestion_events[-1]
    assert selected_index == 1
    assert [suggestion.alias for suggestion in suggestions] == ["/config", "/compact"]


def test_on_key_enter_submits_selected_completion() -> None:
    controller, view = make_controller(prefix="/c")

    controller.on_key(key_event("down"), text="/c", cursor_index=2)

    result = controller.on_key(key_event("enter"), text="/c", cursor_index=2)

    assert result is CompletionResult.SUBMIT
    assert view.replacements == [Replacement(0, 2, "/compact")]
    assert view.reset_count == 1
