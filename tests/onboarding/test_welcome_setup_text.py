"""The welcome screen's plugin-customized text: the typing animation renders
the plugin's line verbatim (no gradient split, no highlight pause), and the
default text is untouched when no plugin customizes it.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from textual.widgets import Static

from vibe.setup.onboarding.screens.welcome import (
    HIGHLIGHT_END,
    WELCOME_TEXT,
    WelcomeScreen,
)

CUSTOM_TEXT = "Welcome to Acme - Let's get you started!"


def _screen(setup_text: str | None = None) -> WelcomeScreen:
    screen = WelcomeScreen(setup_text=setup_text)
    screen._welcome_text = MagicMock(spec=Static)  # type: ignore[assignment]
    return screen


def test_the_default_text_keeps_the_gradient_split() -> None:
    screen = _screen()
    rendered = screen._render_text(HIGHLIGHT_END)
    # The default line splits into prefix + gradient-highlighted brand; the
    # highlight is per-character markup, so the plain brand is not a substring.
    assert rendered.startswith("Welcome to ")
    assert "[bold" in rendered
    assert rendered != WELCOME_TEXT[:HIGHLIGHT_END]


def test_a_custom_text_renders_verbatim_as_one_span() -> None:
    screen = _screen(setup_text=CUSTOM_TEXT)
    # Past the default highlight boundary the default text would gain markup;
    # the custom line stays plain at every length.
    for length in (1, len(CUSTOM_TEXT) // 2, len(CUSTOM_TEXT)):
        assert screen._render_text(length) == CUSTOM_TEXT[:length]


def test_typing_runs_to_the_custom_text_length() -> None:
    screen = _screen(setup_text=CUSTOM_TEXT)
    welcome_text = cast(MagicMock, screen._welcome_text)
    for _ in range(len(CUSTOM_TEXT)):
        screen._type_next_char()
    assert screen._char_index == len(CUSTOM_TEXT)
    rendered = welcome_text.update.call_args
    assert rendered is not None
    assert rendered.args[0] == CUSTOM_TEXT


def test_a_custom_text_skips_the_highlight_pause() -> None:
    screen = _screen(setup_text=CUSTOM_TEXT)
    # Type through the default highlight boundary: no pause is scheduled.
    screen._char_index = HIGHLIGHT_END
    screen._type_next_char()
    assert not screen._paused
    assert screen._char_index == HIGHLIGHT_END + 1


def test_the_default_text_pauses_at_the_highlight_boundary() -> None:
    screen = _screen()
    screen._char_index = HIGHLIGHT_END
    screen.set_timer = MagicMock()  # type: ignore[method-assign]
    screen.set_interval = MagicMock()  # type: ignore[method-assign]
    screen._type_next_char()
    assert screen._paused
    assert screen._char_index == HIGHLIGHT_END
