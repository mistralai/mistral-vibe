from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.timer import Timer
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.braille_renderer import render_braille

WIDTH = 22
HEIGHT = 12
STARTING_DOTS = [
    set[int](),
    {6, 7, 15, 19},
    {5, 8, 14, 16, 18, 20},
    {4, 6, 7, 14, 17, 20},
    {3, 5, 10, 11, 12, 14, 20},
    {3, 5, 9, 13, 14, 16, 18, 20},
    {3, 5, 8, 13, 17, 21},
    {3, 6, 7, 8, 11, 14, 15, 16, 18, 19, 20},
    {4, 5, 8, 12, 17, 19},
    {6, 7, 8, 13, 18, 20},
    {9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20},
    set[int](),
]
QUEUE_RIGHT_TO_MID = {
    "remove": {1j + 6, 1j + 7, 2j + 8, 3j + 4, 3j + 6, 3j + 7, 8j + 4, 8j + 5},
    "add": {1j + 4, 2j + 3, 3j + 3, 3j + 5, 7j + 5, 8j + 3, 9j + 4, 9j + 5},
}
QUEUE_MID_TO_RIGHT = {
    "remove": QUEUE_RIGHT_TO_MID["add"],
    "add": QUEUE_RIGHT_TO_MID["remove"],
}
QUEUE_MID_TO_LEFT = {
    "remove": {1j + 4, 2j + 5, 3j + 3, 3j + 5, 7j + 5, 8j + 3, 9j + 4, 9j + 5},
    "add": {1j + 1, 1j + 2, 2j, 3j + 1, 3j + 2, 3j + 4, 8j + 4, 8j + 5},
}
QUEUE_LEFT_TO_MID = {
    "remove": QUEUE_MID_TO_LEFT["add"],
    "add": QUEUE_MID_TO_LEFT["remove"],
}
WAIT = {"remove": set[int](), "add": set[int]()}
HEAD_RIGHT = {"remove": {5j + 16, 5j + 18, 6j + 17}, "add": {5j + 17, 5j + 19, 6j + 18}}
HEAD_LEFT = {"remove": {5j + 17, 5j + 19, 6j + 18}, "add": {5j + 16, 5j + 18, 6j + 17}}
HEAD_DOWN = {
    "remove": {
        1j + 15,
        1j + 19,
        2j + 14,
        2j + 16,
        2j + 18,
        2j + 20,
        3j + 17,
        5j + 17,
        5j + 19,
        6j + 13,
        6j + 18,
        6j + 21,
        7j + 14,
        7j + 15,
        7j + 16,
        7j + 19,
        7j + 20,
    },
    "add": {
        2j + 15,
        2j + 19,
        3j + 16,
        3j + 18,
        4j + 17,
        6j + 14,
        6j + 17,
        6j + 19,
        6j + 20,
        7j + 13,
        7j + 18,
        7j + 21,
        8j + 14,
        8j + 15,
        8j + 16,
        8j + 18,
        8j + 20,
    },
}
HEAD_UP = {
    "remove": {
        2j + 15,
        2j + 19,
        3j + 16,
        3j + 18,
        4j + 17,
        6j + 14,
        6j + 17,
        6j + 19,
        6j + 20,
        7j + 13,
        7j + 18,
        7j + 21,
        8j + 14,
        8j + 15,
        8j + 16,
        8j + 18,
        8j + 20,
    },
    "add": {
        1j + 15,
        1j + 19,
        2j + 14,
        2j + 16,
        2j + 18,
        2j + 20,
        3j + 17,
        5j + 17,
        5j + 19,
        6j + 13,
        6j + 18,
        6j + 21,
        7j + 14,
        7j + 15,
        7j + 16,
        7j + 18,
        7j + 19,
        7j + 20,
    },
}
BLINK_EYES_HEAD_HIGH = [
    {"remove": {5j + 16, 5j + 18}, "add": set[int]()},
    {"remove": set[int](), "add": {5j + 16, 5j + 18}},
]
BLINK_EYES_HEAD_LOW = [
    {"remove": {6j + 17, 6j + 19}, "add": set[int]()},
    {"remove": set[int](), "add": {6j + 17, 6j + 19}},
]
TRANSITIONS = [
    *BLINK_EYES_HEAD_HIGH,
    WAIT,
    QUEUE_RIGHT_TO_MID,
    HEAD_RIGHT,
    WAIT,
    QUEUE_MID_TO_LEFT,
    WAIT,
    QUEUE_LEFT_TO_MID,
    WAIT,
    HEAD_DOWN,
    WAIT,
    QUEUE_MID_TO_RIGHT,
    *BLINK_EYES_HEAD_LOW,
    WAIT,
    QUEUE_RIGHT_TO_MID,
    WAIT,
    QUEUE_MID_TO_LEFT,
    WAIT,
    HEAD_UP,
    WAIT,
    QUEUE_LEFT_TO_MID,
    HEAD_LEFT,
    WAIT,
    QUEUE_MID_TO_RIGHT,
]
# cf render_braille() docstring for coordinates convention


class PetitChat(Static):
    def __init__(self, animate: bool = True, **kwargs: Any) -> None:
        super().__init__(**kwargs, classes="banner-chat")
        self._dots = {1j * y + x for y, row in enumerate(STARTING_DOTS) for x in row}
        self._transition_index = 0
        self._do_animate = animate
        self._freeze_requested = False
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static(render_braille(self._dots, WIDTH, HEIGHT), classes="petit-chat")

    def on_mount(self) -> None:
        self._inner = self.query_one(".petit-chat", Static)
        if self._do_animate:
            self._timer = self.set_interval(0.16, self._apply_next_transition)

    def freeze_animation(self) -> None:
        self._freeze_requested = True

    def _apply_next_transition(self) -> None:
        if self._freeze_requested and self._transition_index == 0:
            if self._timer:
                self._timer.stop()
            self._timer = None
            return

        transition = TRANSITIONS[self._transition_index]
        self._dots -= transition["remove"]
        self._dots |= transition["add"]
        self._transition_index = (self._transition_index + 1) % len(TRANSITIONS)
        self._inner.update(render_braille(self._dots, WIDTH, HEIGHT))
