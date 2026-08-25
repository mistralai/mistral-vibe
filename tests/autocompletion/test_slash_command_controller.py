from __future__ import annotations

from typing import NamedTuple

from textual import events

from vibe.cli.autocompletion.base import (
    CompletionEntry,
    CompletionResult,
    CompletionView,
)
from vibe.cli.autocompletion.completers import CommandCompleter
from vibe.cli.autocompletion.slash_command import SlashCommandController


class SuggestionEvent(NamedTuple):
    suggestions: list[CompletionEntry]
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
        self, suggestions: list[CompletionEntry], selected_index: int
    ) -> None:
        self.suggestion_events.append(
            SuggestionEvent(list(suggestions), selected_index)
        )

    def clear_completion_suggestions(self) -> None:
        self.reset_count += 1

    def replace_completion_range(
        self, start: int, end: int, replacement: str, *, suppress_update: bool = False
    ) -> None:
        self.replacements.append(Replacement(start, end, replacement))


def key_event(key: str) -> events.Key:
    return events.Key(key, character=None)


def make_controller(
    *, prefix: str | None = None
) -> tuple[SlashCommandController, StubView]:
    commands = [
        CompletionEntry("/config", "Show current configuration"),
        CompletionEntry("/compact", "Compact history"),
        CompletionEntry("/help", "Display help"),
        CompletionEntry("/config", "Override description"),
        CompletionEntry("/summarize", "Summarize history"),
        CompletionEntry("/logpath", "Show log path"),
        CompletionEntry("/exit", "Exit application"),
        CompletionEntry("/vim", "Toggle vim keybindings"),
    ]
    completer = CommandCompleter(lambda: commands)
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
        CompletionEntry("/config", "Override description"),
        CompletionEntry("/compact", "Compact history"),
    ]
    assert selected == 0


def test_on_text_change_filters_suggestions_case_insensitively() -> None:
    controller, view = make_controller(prefix="/c")

    controller.on_text_changed("/CO", cursor_index=3)

    suggestions, _ = view.suggestion_events[-1]
    assert [suggestion.label for suggestion in suggestions] == ["/config", "/compact"]


def test_on_text_change_clears_suggestions_when_no_matches() -> None:
    controller, view = make_controller(prefix="/c")

    controller.on_text_changed("/c", cursor_index=2)
    controller.on_text_changed("config", cursor_index=6)

    assert view.reset_count >= 1


def test_on_text_change_limits_the_number_of_results_and_preserves_insertion_order() -> (
    None
):
    controller, view = make_controller(prefix="/")

    controller.on_text_changed("/", cursor_index=1)

    suggestions, selected_index = view.suggestion_events[-1]
    assert len(suggestions) == 7
    assert [suggestion.label for suggestion in suggestions] == [
        "/help",
        "/config",
        "/compact",
        "/summarize",
        "/logpath",
        "/exit",
        "/vim",
    ]


def test_on_key_tab_applies_selected_completion() -> None:
    controller, view = make_controller(prefix="/c")

    result = controller.on_key(key_event("tab"), text="/c", cursor_index=2)

    assert result is CompletionResult.HANDLED
    assert view.replacements == [Replacement(0, 2, "/config")]
    assert view.reset_count == 1


def test_on_key_tab_replaces_whole_command_word_when_caret_mid_token() -> None:
    controller, view = make_controller()
    controller.on_text_changed("/config", cursor_index=len("/config"))

    # The caret moved into the middle of the token (via arrow key or click)
    # before accepting; the whole command word must be replaced, not a prefix,
    # so the accepted command is not corrupted with a leftover tail.
    controller.on_key(key_event("tab"), text="/config", cursor_index=3)

    assert view.replacements[-1] == Replacement(0, len("/config"), "/config")


def test_on_key_tab_preserves_text_after_newline() -> None:
    controller, view = make_controller()
    text = "/config\nfoo"
    controller.on_text_changed(text, cursor_index=len("/config"))

    # Chat input allows newlines (shift+enter); accepting the command must
    # replace only the command word, not wipe the following line.
    controller.on_key(key_event("tab"), text=text, cursor_index=len("/config"))

    assert view.replacements[-1] == Replacement(0, len("/config"), "/config")


def test_selection_survives_re_render_with_unchanged_suggestions() -> None:
    controller, view = make_controller(prefix="/c")
    controller.on_key(key_event("down"), text="/c", cursor_index=2)
    assert view.suggestion_events[-1].selected_index == 1

    # A caret move re-runs on_text_changed with the same text/suggestions; the
    # highlighted item must not jump back to the top.
    controller.on_text_changed("/c", cursor_index=2)

    assert view.suggestion_events[-1].selected_index == 1


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
    assert [suggestion.label for suggestion in suggestions] == ["/config", "/compact"]


def test_on_key_enter_submits_selected_completion() -> None:
    controller, view = make_controller(prefix="/c")

    controller.on_key(key_event("down"), text="/c", cursor_index=2)

    result = controller.on_key(key_event("enter"), text="/c", cursor_index=2)

    assert result is CompletionResult.SUBMIT
    assert view.replacements == [Replacement(0, 2, "/compact")]
    assert view.reset_count == 1


def test_callable_entries_updates_completions_dynamically() -> None:
    """Test that CommandCompleter with a callable updates entries when the callable returns different values.

    This simulates config reload where available skills change.
    """
    available_skills: list[CompletionEntry] = []

    def get_entries() -> list[CompletionEntry]:
        base_commands = [
            CompletionEntry("/help", "Display help"),
            CompletionEntry("/config", "Show configuration"),
        ]
        return base_commands + available_skills

    completer = CommandCompleter(get_entries)
    view = StubView()
    controller = SlashCommandController(completer, view)

    # Initially, only base commands are available
    controller.on_text_changed("/", cursor_index=1)
    suggestions, _ = view.suggestion_events[-1]
    assert [s.label for s in suggestions] == ["/help", "/config"]

    # Simulate config reload: add a skill
    available_skills.append(CompletionEntry("/summarize", "Summarize the conversation"))

    # Now completions should include the new skill
    controller.on_text_changed("/", cursor_index=1)
    suggestions, _ = view.suggestion_events[-1]
    assert [s.label for s in suggestions] == ["/help", "/config", "/summarize"]

    # And searching for "/s" should find the new skill
    controller.on_text_changed("/s", cursor_index=2)
    suggestions, _ = view.suggestion_events[-1]
    assert [s.label for s in suggestions] == ["/summarize"]
    assert suggestions[0].description == "Summarize the conversation"


def test_tab_on_slash_command_with_args_replaces_only_head() -> None:
    controller, view = make_controller()
    text = "/compact some args"
    controller.on_text_changed(text, cursor_index=len(text))

    result = controller.on_key(key_event("tab"), text=text, cursor_index=len(text))

    assert result is CompletionResult.HANDLED
    assert view.replacements == [Replacement(0, 8, "/compact")]


def test_enter_on_slash_command_with_args_submits_with_head_only_replacement() -> None:
    controller, view = make_controller()
    text = "/compact some args"
    controller.on_text_changed(text, cursor_index=len(text))

    result = controller.on_key(key_event("enter"), text=text, cursor_index=len(text))

    assert result is CompletionResult.SUBMIT
    assert view.replacements == [Replacement(0, 8, "/compact")]


def test_on_text_change_matches_substring_not_just_prefix() -> None:
    controller, view = make_controller()

    controller.on_text_changed("/path", cursor_index=5)

    suggestions, _ = view.suggestion_events[-1]
    assert any(s.label == "/logpath" for s in suggestions)


def test_on_text_change_matches_middle_segment_of_hyphenated_command() -> None:
    commands = [
        CompletionEntry("/foo-bar-skill", "A skill"),
        CompletionEntry("/baz-bar-other", "Another skill"),
        CompletionEntry("/unrelated", "No match"),
    ]
    completer = CommandCompleter(lambda: commands)
    view = StubView()
    controller = SlashCommandController(completer, view)

    controller.on_text_changed("/bar", cursor_index=4)

    suggestions, _ = view.suggestion_events[-1]
    aliases = [s.label for s in suggestions]
    assert "/foo-bar-skill" in aliases
    assert "/baz-bar-other" in aliases
    assert "/unrelated" not in aliases


def test_on_text_change_fuzzy_matches_scattered_characters() -> None:
    controller, view = make_controller()

    controller.on_text_changed("/sm", cursor_index=3)

    suggestions, _ = view.suggestion_events[-1]
    assert any(s.label == "/summarize" for s in suggestions)


def test_on_text_change_fuzzy_ranks_prefix_matches_higher() -> None:
    commands = [
        CompletionEntry("/zoo-config", "Zoo config"),
        CompletionEntry("/config", "Main config"),
    ]
    completer = CommandCompleter(lambda: commands)
    view = StubView()
    controller = SlashCommandController(completer, view)

    controller.on_text_changed("/config", cursor_index=7)

    suggestions, _ = view.suggestion_events[-1]
    aliases = [s.label for s in suggestions]
    assert aliases.index("/config") < aliases.index("/zoo-config")


def test_callable_entries_reflects_enabled_disabled_skills() -> None:
    """Test that skill enable/disable changes are reflected in completions.

    This simulates the scenario where a user changes enabled_skills in config
    and runs /reload.
    """
    enabled_skills: set[str] = {"commit", "review"}

    all_skills = [
        CompletionEntry("/commit", "Create a git commit"),
        CompletionEntry("/review", "Review code changes"),
        CompletionEntry("/deploy", "Deploy to production"),
    ]

    def get_entries() -> list[CompletionEntry]:
        return [s for s in all_skills if s.label[1:] in enabled_skills]

    completer = CommandCompleter(get_entries)
    view = StubView()
    controller = SlashCommandController(completer, view)

    # Initially only commit and review are enabled
    controller.on_text_changed("/", cursor_index=1)
    suggestions, _ = view.suggestion_events[-1]
    assert [s.label for s in suggestions] == ["/commit", "/review"]

    # Simulate config reload: enable deploy, disable commit
    enabled_skills.discard("commit")
    enabled_skills.add("deploy")

    # Now completions should reflect the change
    controller.on_text_changed("/", cursor_index=1)
    suggestions, _ = view.suggestion_events[-1]
    assert [s.label for s in suggestions] == ["/review", "/deploy"]
