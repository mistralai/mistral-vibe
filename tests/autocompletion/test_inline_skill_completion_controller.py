from __future__ import annotations

from typing import NamedTuple

from textual import events

from vibe.cli.autocompletion.base import CompletionResult
from vibe.cli.autocompletion.inline_skill_completion import (
    InlineSkillCompletionController,
)

SKILLS: list[tuple[str, str]] = [
    ("/polco", "Make user feel good"),
    ("/polish", "Polish the prose"),
    ("/review", "Review code changes"),
]


class Replacement(NamedTuple):
    start: int
    end: int
    replacement: str
    suppress_update: bool


class StubView:
    def __init__(self) -> None:
        self.suggestion: str | None = None
        self.clear_count = 0
        self.replacements: list[Replacement] = []

    def show_inline_suggestion(self, suggestion: str) -> None:
        self.suggestion = suggestion

    def clear_inline_suggestion(self) -> None:
        self.suggestion = None
        self.clear_count += 1

    def replace_completion_range(
        self, start: int, end: int, replacement: str, *, suppress_update: bool = False
    ) -> None:
        self.replacements.append(Replacement(start, end, replacement, suppress_update))


def key_event(key: str) -> events.Key:
    return events.Key(key, character=None)


def make_controller(
    *, is_default_mode: bool = True
) -> tuple[InlineSkillCompletionController, StubView]:
    view = StubView()
    controller = InlineSkillCompletionController(
        lambda: list(SKILLS), view, lambda: is_default_mode
    )
    return controller, view


def test_can_handle_mid_prompt_slash_word() -> None:
    controller, _ = make_controller()
    text = "run /pol"
    assert controller.can_handle(text, cursor_index=len(text))


def test_does_not_handle_leading_slash_word() -> None:
    controller, _ = make_controller()
    text = "/pol"
    assert not controller.can_handle(text, cursor_index=len(text))


def test_does_not_handle_in_non_default_mode() -> None:
    # In bash/slash/teleport mode the ghost never activates, even mid-prompt.
    controller, _ = make_controller(is_default_mode=False)
    text = "!ls /pol"
    assert not controller.can_handle(text, cursor_index=len(text))


def test_handles_default_mode_text_starting_with_mode_char() -> None:
    # Default-mode text can start with !, /, & via paste or the external editor
    # without being a real mode; mid-prompt ghosting must still work.
    controller, view = make_controller()
    text = "!note /pol"
    controller.on_text_changed(text, cursor_index=len(text))
    assert view.suggestion == "co"


def test_does_not_handle_when_cursor_inside_word() -> None:
    controller, _ = make_controller()
    text = "run /polco"
    # cursor sits in the middle of the token (before "co")
    assert not controller.can_handle(text, cursor_index=len("run /pol"))


def test_does_not_handle_path_like_token() -> None:
    controller, _ = make_controller()
    text = "open /etc/hosts"
    assert not controller.can_handle(text, cursor_index=len(text))


def test_does_not_handle_when_only_whitespace_before_token() -> None:
    controller, _ = make_controller()
    # Leading space or a blank first line still makes /pol the first word, which
    # belongs to the slash popup rather than the mid-prompt ghost.
    assert not controller.can_handle(" /pol", cursor_index=len(" /pol"))
    assert not controller.can_handle("\n/pol", cursor_index=len("\n/pol"))


def test_on_text_changed_previews_best_match_suffix() -> None:
    controller, view = make_controller()
    text = "run /pol"

    controller.on_text_changed(text, cursor_index=len(text))

    assert view.suggestion == "co"


def test_on_text_changed_is_case_insensitive() -> None:
    controller, view = make_controller()
    text = "run /POL"

    controller.on_text_changed(text, cursor_index=len(text))

    assert view.suggestion == "co"


def test_bare_slash_does_not_preview() -> None:
    controller, view = make_controller()
    text = "run /"

    assert not controller.can_handle(text, cursor_index=len(text))
    controller.on_text_changed(text, cursor_index=len(text))

    assert view.suggestion is None


def test_on_text_changed_clears_when_no_match() -> None:
    controller, view = make_controller()
    controller.on_text_changed("run /pol", cursor_index=len("run /pol"))
    assert view.suggestion == "co"

    controller.on_text_changed("run /zzz", cursor_index=len("run /zzz"))

    assert view.suggestion is None


def test_fully_typed_skill_has_no_suffix() -> None:
    controller, view = make_controller()
    text = "run /polco"

    controller.on_text_changed(text, cursor_index=len(text))

    assert view.suggestion is None


def test_tab_accepts_and_replaces_token() -> None:
    controller, view = make_controller()
    text = "run /pol"
    controller.on_text_changed(text, cursor_index=len(text))

    result = controller.on_key(key_event("tab"), text, cursor_index=len(text))

    assert result is CompletionResult.HANDLED
    # suppress_update is False so the widget re-emits on_text_changed and a
    # deeper skill can surface its ghost, matching right-arrow acceptance.
    assert view.replacements == [Replacement(4, 8, "/polco", False)]
    assert view.suggestion is None


def test_tab_without_match_is_ignored() -> None:
    controller, view = make_controller()

    result = controller.on_key(key_event("tab"), "run /zzz", cursor_index=8)

    assert result is CompletionResult.IGNORED
    assert view.replacements == []


def test_navigation_key_is_ignored_and_leaves_ghost_to_text_area() -> None:
    controller, view = make_controller()
    text = "run /pol"
    controller.on_text_changed(text, cursor_index=len(text))

    # Nav keys are no longer handled here; the text area re-evaluates on caret
    # move (arrows, word/line nav, clicks). The controller leaves the ghost.
    result = controller.on_key(key_event("left"), text, cursor_index=len(text))

    assert result is CompletionResult.IGNORED
    assert view.suggestion == "co"


def test_caret_move_off_token_clears_ghost() -> None:
    controller, view = make_controller()
    controller.on_text_changed("run /pol", cursor_index=len("run /pol"))
    assert view.suggestion == "co"

    # The text area re-evaluates after the caret moved into "run".
    controller.on_text_changed("run /pol", cursor_index=2)

    assert view.suggestion is None


def test_is_showing_reflects_ghost_state() -> None:
    controller, _ = make_controller()
    assert controller.is_showing() is False

    controller.on_text_changed("run /pol", cursor_index=len("run /pol"))
    assert controller.is_showing() is True

    controller.reset()
    assert controller.is_showing() is False


def test_escape_is_left_to_the_app_not_dismissed_by_controller() -> None:
    controller, view = make_controller()
    text = "run /pol"
    controller.on_text_changed(text, cursor_index=len(text))

    result = controller.on_key(key_event("escape"), text, cursor_index=len(text))

    assert result is CompletionResult.IGNORED
    assert view.suggestion == "co"
    # Escape is not a controller dismiss key; the ghost stays until the app clears it.
    assert controller.is_showing() is True


def test_tab_ignored_when_cursor_moved_off_token() -> None:
    controller, view = make_controller()
    text = "run /pol"
    controller.on_text_changed(text, cursor_index=len(text))

    # A mouse click moved the caret into "run" without any text change, so the
    # stored span is stale; Tab must not rewrite the old token range.
    result = controller.on_key(key_event("tab"), text, cursor_index=2)

    assert result is CompletionResult.IGNORED
    assert view.replacements == []
    assert view.suggestion is None


def test_tab_ignored_when_text_changed_under_stale_match() -> None:
    controller, view = make_controller()
    controller.on_text_changed("run /pol", cursor_index=len("run /pol"))

    # Same cursor offset, but the token no longer prefixes the stored match.
    result = controller.on_key(key_event("tab"), "run /xyz", cursor_index=8)

    assert result is CompletionResult.IGNORED
    assert view.replacements == []
    assert view.suggestion is None


def test_other_key_is_ignored_and_keeps_ghost() -> None:
    controller, view = make_controller()
    text = "run /pol"
    controller.on_text_changed(text, cursor_index=len(text))

    result = controller.on_key(key_event("a"), text, cursor_index=len(text))

    assert result is CompletionResult.IGNORED
    assert view.suggestion == "co"


def test_slash_at_start_of_second_line_is_handled() -> None:
    controller, view = make_controller()
    text = "run\n/pol"

    assert controller.can_handle(text, cursor_index=len(text))
    controller.on_text_changed(text, cursor_index=len(text))
    assert view.suggestion == "co"
