from __future__ import annotations

from textual import events

from vibe.cli.autocompletion.base import CompletionResult
from vibe.cli.autocompletion.inline_skill_completion import (
    InlineSkillCompletionController,
)
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)

SKILLS: list[tuple[str, str]] = [("/polco", "Make user feel good")]


# Controller double whose visibility and reset calls can be inspected.
class RecordingController:
    def __init__(self, *, showing: bool) -> None:
        self._showing = showing
        self.reset_calls = 0

    def can_handle(self, text: str, cursor_index: int) -> bool:
        return True

    def on_text_changed(self, text: str, cursor_index: int) -> None: ...

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        return CompletionResult.IGNORED

    def is_showing(self) -> bool:
        return self._showing

    def reset(self) -> None:
        self.reset_calls += 1
        self._showing = False


class StubView:
    def __init__(self) -> None:
        self.suggestion: str | None = None

    def show_inline_suggestion(self, suggestion: str) -> None:
        self.suggestion = suggestion

    def clear_inline_suggestion(self) -> None:
        self.suggestion = None

    def replace_completion_range(
        self, start: int, end: int, replacement: str, *, suppress_update: bool = False
    ) -> None: ...


def key_event(key: str) -> events.Key:
    return events.Key(key, character=None)


def make_manager() -> tuple[MultiCompletionManager, StubView]:
    view = StubView()
    inline = InlineSkillCompletionController(lambda: list(SKILLS), view, lambda: True)
    return MultiCompletionManager([inline]), view


def test_has_active_completion_when_ghost_visible() -> None:
    manager, _ = make_manager()
    text = "run /pol"
    manager.on_text_changed(text, cursor_index=len(text))

    assert manager.has_active_completion() is True


def test_no_active_completion_after_caret_moves_off_token() -> None:
    manager, view = make_manager()
    text = "run /pol"
    manager.on_text_changed(text, cursor_index=len(text))
    assert manager.has_active_completion() is True

    # The text area re-evaluates when the caret moves (arrow, word-nav, click);
    # moving off the token clears the ghost and deactivates the manager.
    manager.on_text_changed(text, cursor_index=2)

    assert view.suggestion is None
    assert manager.has_active_completion() is False


def test_no_active_completion_when_no_match() -> None:
    manager, _ = make_manager()
    manager.on_text_changed("run /zzz", cursor_index=len("run /zzz"))

    assert manager.has_active_completion() is False


def test_escape_leaves_ghost_for_the_app_to_dismiss() -> None:
    manager, view = make_manager()
    text = "run /pol"
    manager.on_text_changed(text, cursor_index=len(text))

    # Escape is not a controller dismiss key, so the ghost survives on_key and
    # the manager still reports it — the app then clears it via reset().
    manager.on_key(key_event("escape"), text, cursor_index=len(text))
    assert view.suggestion == "co"
    assert manager.has_active_completion() is True

    manager.reset()
    assert view.suggestion is None
    assert manager.has_active_completion() is False


def test_dismiss_resets_active_controller_even_when_not_showing() -> None:
    controller = RecordingController(showing=False)
    manager = MultiCompletionManager([controller])
    manager.on_text_changed("@foo", cursor_index=4)

    # Nothing is on screen (e.g. a path query still in flight), so the key is
    # not consumed — but the controller must still be reset to cancel that work.
    assert manager.dismiss() is False
    assert controller.reset_calls == 1


def test_dismiss_reports_and_clears_a_visible_completion() -> None:
    controller = RecordingController(showing=True)
    manager = MultiCompletionManager([controller])
    manager.on_text_changed("@foo", cursor_index=4)

    assert manager.dismiss() is True
    assert controller.reset_calls == 1
