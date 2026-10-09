from __future__ import annotations

from enum import Enum, auto
import random
from typing import Any

from textual.app import ComposeResult
from textual.timer import Timer
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.braille_renderer import render_braille

WIDTH = 22
HEIGHT = 12
LECHONK_WIDTH = 26
LECHONK_HEIGHT = 15
FRAME_INTERVAL_S = 0.16
CYCLE_DELAY_MIN_S = 5.0
CYCLE_DELAY_MAX_S = 20.0
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

# Transition indices reached right after a head settles into a direction, with
# the eyes open. The cat may rest at any of these, on top of the end of cycle.
EYES_OPEN_PAUSE_FRAMES = frozenset({5, 11, 21, 24})
MID_CYCLE_PAUSE_CHANCE = 0.25


# ── LeChonk frames (from pixilart, 26×15, 26 transitions) ──

LECHONK_STARTING_DOTS: list[set[int]] = [
    set[int](),
    set[int](),
    set[int](),
    set[int](),
    {11, 12, 13, 14, 15, 17, 21},
    {10, 16, 18, 20, 22},
    {9, 16, 19, 22},
    {8, 16, 22},
    {6, 7, 8, 15, 18, 20, 23},
    {5, 8, 15, 19, 23},
    {4, 6, 7, 8, 16, 17, 18, 20, 21, 22},
    {2, 3, 4, 6, 7, 14, 15, 18, 21},
    {1, 5, 7, 16, 19, 22},
    {2, 3, 4, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22},
    set[int](),
]

_LECHONK_T00_REMOVE: set[complex] = {18 + 8j, 20 + 8j, 19 + 9j}
_LECHONK_T00_ADD: set[complex] = set()

_LECHONK_T01_REMOVE: set[complex] = set()
_LECHONK_T01_ADD: set[complex] = {18 + 8j, 20 + 8j, 19 + 9j}

_LECHONK_T02_REMOVE: set[complex] = set()
_LECHONK_T02_ADD: set[complex] = set()

_LECHONK_T03_REMOVE: set[complex] = {
    5 + 9j,
    2 + 11j,
    3 + 11j,
    4 + 11j,
    6 + 11j,
    1 + 12j,
    5 + 12j,
    2 + 13j,
    3 + 13j,
    4 + 13j,
}
_LECHONK_T03_ADD: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    4 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    5 + 10j,
}

_LECHONK_T04_REMOVE: set[complex] = {18 + 8j, 20 + 8j, 19 + 9j}
_LECHONK_T04_ADD: set[complex] = {19 + 8j, 21 + 8j, 20 + 9j}

_LECHONK_T05_REMOVE: set[complex] = set()
_LECHONK_T05_ADD: set[complex] = set()

_LECHONK_T06_REMOVE: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    4 + 10j,
    5 + 10j,
}
_LECHONK_T06_ADD: set[complex] = {
    2 + 5j,
    3 + 5j,
    4 + 5j,
    1 + 6j,
    5 + 6j,
    2 + 7j,
    3 + 7j,
    4 + 7j,
    6 + 7j,
    5 + 9j,
}

_LECHONK_T07_REMOVE: set[complex] = set()
_LECHONK_T07_ADD: set[complex] = set()

_LECHONK_T08_REMOVE: set[complex] = {
    2 + 5j,
    3 + 5j,
    4 + 5j,
    1 + 6j,
    5 + 6j,
    2 + 7j,
    3 + 7j,
    4 + 7j,
    6 + 7j,
    5 + 9j,
}
_LECHONK_T08_ADD: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    4 + 10j,
    5 + 10j,
}

_LECHONK_T09_REMOVE: set[complex] = set()
_LECHONK_T09_ADD: set[complex] = set()

_LECHONK_T10_REMOVE: set[complex] = {
    17 + 4j,
    21 + 4j,
    18 + 5j,
    20 + 5j,
    22 + 5j,
    19 + 6j,
    15 + 8j,
    19 + 8j,
    21 + 8j,
    23 + 8j,
    20 + 9j,
    16 + 10j,
    17 + 10j,
    18 + 10j,
    21 + 10j,
    22 + 10j,
}
_LECHONK_T10_ADD: set[complex] = {
    17 + 5j,
    21 + 5j,
    18 + 6j,
    20 + 6j,
    19 + 7j,
    16 + 8j,
    22 + 8j,
    19 + 9j,
    21 + 9j,
    15 + 10j,
    23 + 10j,
    16 + 11j,
    17 + 11j,
    20 + 11j,
    22 + 11j,
}

_LECHONK_T11_REMOVE: set[complex] = set()
_LECHONK_T11_ADD: set[complex] = set()

_LECHONK_T12_REMOVE: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    4 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    5 + 10j,
}
_LECHONK_T12_ADD: set[complex] = {
    5 + 9j,
    2 + 11j,
    3 + 11j,
    4 + 11j,
    6 + 11j,
    1 + 12j,
    5 + 12j,
    2 + 13j,
    3 + 13j,
    4 + 13j,
}

_LECHONK_T13_REMOVE: set[complex] = {19 + 9j, 21 + 9j, 20 + 10j}
_LECHONK_T13_ADD: set[complex] = set()

_LECHONK_T14_REMOVE: set[complex] = set()
_LECHONK_T14_ADD: set[complex] = {19 + 9j, 21 + 9j, 20 + 10j}

_LECHONK_T15_REMOVE: set[complex] = set()
_LECHONK_T15_ADD: set[complex] = set()

_LECHONK_T16_REMOVE: set[complex] = {
    5 + 9j,
    2 + 11j,
    3 + 11j,
    4 + 11j,
    6 + 11j,
    1 + 12j,
    5 + 12j,
    2 + 13j,
    3 + 13j,
    4 + 13j,
}
_LECHONK_T16_ADD: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    4 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    5 + 10j,
}

_LECHONK_T17_REMOVE: set[complex] = set()
_LECHONK_T17_ADD: set[complex] = set()

_LECHONK_T18_REMOVE: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    4 + 10j,
    5 + 10j,
}
_LECHONK_T18_ADD: set[complex] = {
    2 + 5j,
    3 + 5j,
    4 + 5j,
    1 + 6j,
    5 + 6j,
    2 + 7j,
    3 + 7j,
    4 + 7j,
    6 + 7j,
    5 + 9j,
}

_LECHONK_T19_REMOVE: set[complex] = set()
_LECHONK_T19_ADD: set[complex] = set()

_LECHONK_T20_REMOVE: set[complex] = {
    17 + 5j,
    21 + 5j,
    18 + 6j,
    20 + 6j,
    19 + 7j,
    16 + 8j,
    22 + 8j,
    19 + 9j,
    21 + 9j,
    15 + 10j,
    23 + 10j,
    16 + 11j,
    17 + 11j,
    20 + 11j,
    22 + 11j,
}
_LECHONK_T20_ADD: set[complex] = {
    17 + 4j,
    21 + 4j,
    18 + 5j,
    20 + 5j,
    22 + 5j,
    19 + 6j,
    15 + 8j,
    19 + 8j,
    21 + 8j,
    23 + 8j,
    20 + 9j,
    16 + 10j,
    17 + 10j,
    18 + 10j,
    21 + 10j,
    22 + 10j,
}

_LECHONK_T21_REMOVE: set[complex] = set()
_LECHONK_T21_ADD: set[complex] = set()

_LECHONK_T22_REMOVE: set[complex] = {
    2 + 5j,
    3 + 5j,
    4 + 5j,
    1 + 6j,
    5 + 6j,
    2 + 7j,
    3 + 7j,
    4 + 7j,
    6 + 7j,
    5 + 9j,
}
_LECHONK_T22_ADD: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    4 + 10j,
    5 + 10j,
}

_LECHONK_T23_REMOVE: set[complex] = {19 + 8j, 21 + 8j, 20 + 9j}
_LECHONK_T23_ADD: set[complex] = {18 + 8j, 20 + 8j, 19 + 9j}

_LECHONK_T24_REMOVE: set[complex] = set()
_LECHONK_T24_ADD: set[complex] = set()

_LECHONK_T25_REMOVE: set[complex] = {
    1 + 8j,
    2 + 8j,
    3 + 8j,
    4 + 8j,
    5 + 8j,
    0 + 9j,
    1 + 10j,
    2 + 10j,
    3 + 10j,
    5 + 10j,
}
_LECHONK_T25_ADD: set[complex] = {
    5 + 9j,
    2 + 11j,
    3 + 11j,
    4 + 11j,
    6 + 11j,
    1 + 12j,
    5 + 12j,
    2 + 13j,
    3 + 13j,
    4 + 13j,
}

LECHONK_TRANSITIONS: list[dict[str, set[complex]]] = [
    {"remove": _LECHONK_T00_REMOVE, "add": _LECHONK_T00_ADD},
    {"remove": _LECHONK_T01_REMOVE, "add": _LECHONK_T01_ADD},
    {"remove": _LECHONK_T02_REMOVE, "add": _LECHONK_T02_ADD},
    {"remove": _LECHONK_T03_REMOVE, "add": _LECHONK_T03_ADD},
    {"remove": _LECHONK_T04_REMOVE, "add": _LECHONK_T04_ADD},
    {"remove": _LECHONK_T05_REMOVE, "add": _LECHONK_T05_ADD},
    {"remove": _LECHONK_T06_REMOVE, "add": _LECHONK_T06_ADD},
    {"remove": _LECHONK_T07_REMOVE, "add": _LECHONK_T07_ADD},
    {"remove": _LECHONK_T08_REMOVE, "add": _LECHONK_T08_ADD},
    {"remove": _LECHONK_T09_REMOVE, "add": _LECHONK_T09_ADD},
    {"remove": _LECHONK_T10_REMOVE, "add": _LECHONK_T10_ADD},
    {"remove": _LECHONK_T11_REMOVE, "add": _LECHONK_T11_ADD},
    {"remove": _LECHONK_T12_REMOVE, "add": _LECHONK_T12_ADD},
    {"remove": _LECHONK_T13_REMOVE, "add": _LECHONK_T13_ADD},
    {"remove": _LECHONK_T14_REMOVE, "add": _LECHONK_T14_ADD},
    {"remove": _LECHONK_T15_REMOVE, "add": _LECHONK_T15_ADD},
    {"remove": _LECHONK_T16_REMOVE, "add": _LECHONK_T16_ADD},
    {"remove": _LECHONK_T17_REMOVE, "add": _LECHONK_T17_ADD},
    {"remove": _LECHONK_T18_REMOVE, "add": _LECHONK_T18_ADD},
    {"remove": _LECHONK_T19_REMOVE, "add": _LECHONK_T19_ADD},
    {"remove": _LECHONK_T20_REMOVE, "add": _LECHONK_T20_ADD},
    {"remove": _LECHONK_T21_REMOVE, "add": _LECHONK_T21_ADD},
    {"remove": _LECHONK_T22_REMOVE, "add": _LECHONK_T22_ADD},
    {"remove": _LECHONK_T23_REMOVE, "add": _LECHONK_T23_ADD},
    {"remove": _LECHONK_T24_REMOVE, "add": _LECHONK_T24_ADD},
    {"remove": _LECHONK_T25_REMOVE, "add": _LECHONK_T25_ADD},
]


class CatVariant(Enum):
    """Which cat sprite to animate."""

    LECHAT = auto()
    LECHONK = auto()

    @classmethod
    def from_model(cls, model: str) -> CatVariant:
        """Pick the variant from a model display name or alias.

        "ml4", "mistral large" (or any "mistral-large-*" API alias),
        "le-chaton-fat", "le-gros-chaton", "le-chonk" → LeChonk.
        Anything else → LeChat.
        """
        lower = model.lower()
        # Normalize hyphens to spaces so "mistral large" catches both
        # "Mistral Large" and "mistral-large-latest" / "mistral-large-4" etc.
        normalized = lower.replace("-", " ")
        if (
            "ml4" in normalized
            or "mistral large" in normalized
            or "le-chaton-fat" in lower
            or "le-gros-chaton" in lower
            or "le-chonk" in lower
        ):
            return cls.LECHONK
        return cls.LECHAT

    @property
    def starting_dots(self) -> list[set[int]]:
        if self is CatVariant.LECHONK:
            return LECHONK_STARTING_DOTS
        return STARTING_DOTS

    @property
    def transitions(self) -> list[dict[str, set[complex]]]:
        if self is CatVariant.LECHONK:
            return LECHONK_TRANSITIONS
        return TRANSITIONS

    @property
    def width(self) -> int:
        return LECHONK_WIDTH if self is CatVariant.LECHONK else WIDTH

    @property
    def height(self) -> int:
        return LECHONK_HEIGHT if self is CatVariant.LECHONK else HEIGHT


class PetitChat(Static):
    def __init__(self, animate: bool = True, **kwargs: Any) -> None:
        classes = kwargs.pop("classes", None)
        merged_classes = "banner-chat" if classes is None else f"banner-chat {classes}"
        super().__init__(**kwargs, classes=merged_classes)
        self._variant = CatVariant.LECHAT
        self._dots = {
            1j * y + x for y, row in enumerate(self._variant.starting_dots) for x in row
        }
        self._transition_index = 0
        self._do_animate = animate
        self._freeze_requested = False
        self._timer: Timer | None = None
        self._resume_frame: int | None = None

    def set_variant(self, variant: CatVariant) -> None:
        """Switch to a different cat variant, resetting the animation."""
        if self._variant is variant:
            return
        self._variant = variant
        self._dots = {
            1j * y + x for y, row in enumerate(variant.starting_dots) for x in row
        }
        self._transition_index = 0
        self._resume_frame = None
        # Toggle the `lechonk` class so the pinned widget sizes follow the
        # variant (see the `.banner-chat` rules in app.tcss).
        self.set_class(variant is CatVariant.LECHONK, "lechonk")
        # Re-render with the new variant's dimensions.
        self._inner.update(
            render_braille(self._dots, variant.width, variant.height), layout=False
        )

    def compose(self) -> ComposeResult:
        yield Static(
            render_braille(self._dots, self._variant.width, self._variant.height),
            classes="petit-chat",
        )

    def on_mount(self) -> None:
        self._inner = self.query_one(".petit-chat", Static)
        if self._do_animate:
            self._timer = self.set_interval(
                FRAME_INTERVAL_S, self._apply_next_transition
            )

    def freeze_animation(self) -> None:
        self._freeze_requested = True

    def _apply_next_transition(self) -> None:
        if self._freeze_requested and self._transition_index == 0:
            if self._timer:
                self._timer.stop()
            self._timer = None
            return

        transitions = self._variant.transitions
        transition = transitions[self._transition_index]
        self._dots -= transition["remove"]
        self._dots |= transition["add"]
        self._transition_index = (self._transition_index + 1) % len(transitions)
        # render_braille always emits the full grid, so frames never change size.
        self._inner.update(
            render_braille(self._dots, self._variant.width, self._variant.height),
            layout=False,
        )

        if not self._may_stop():
            return

        if self._transition_index == 0 or (
            self._transition_index in EYES_OPEN_PAUSE_FRAMES
            and random.random() < MID_CYCLE_PAUSE_CHANCE
        ):
            self._pause_between_cycles()

    def _may_stop(self) -> bool:
        # After a stop, play one full cycle back to the frame we stopped at
        # before considering any new stop.
        if self._resume_frame is None:
            return True
        if self._transition_index == self._resume_frame:
            self._resume_frame = None
            return True
        return False

    def _pause_between_cycles(self) -> None:
        if self._timer:
            self._timer.stop()
        self._resume_frame = self._transition_index
        delay = random.uniform(CYCLE_DELAY_MIN_S, CYCLE_DELAY_MAX_S)
        self._timer = self.set_timer(delay, self._resume_animation)

    def _resume_animation(self) -> None:
        if self._freeze_requested:
            self._timer = None
            return
        self._timer = self.set_interval(FRAME_INTERVAL_S, self._apply_next_transition)
