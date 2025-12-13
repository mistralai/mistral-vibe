from __future__ import annotations

from abc import ABC
from enum import Enum
from typing import ClassVar


class Spinner(ABC):
    FRAMES: ClassVar[tuple[str, ...]]

    def __init__(self) -> None:
        self._position = 0

    def next_frame(self) -> str:
        frame = self.FRAMES[self._position]
        self._position = (self._position + 1) % len(self.FRAMES)
        return frame

    def current_frame(self) -> str:
        return self.FRAMES[self._position]

    def reset(self) -> None:
        self._position = 0


class BrailleSpinner(Spinner):
    FRAMES: ClassVar[tuple[str, ...]] = (
        "⠋",
        "⠙",
        "⠹",
        "⠸",
        "⠼",
        "⠴",
        "⠦",
        "⠧",
        "⠇",
        "⠏",
    )


class LineSpinner(Spinner):
    FRAMES: ClassVar[tuple[str, ...]] = ("|", "/", "-", "\\")


class CircleSpinner(Spinner):
    FRAMES: ClassVar[tuple[str, ...]] = ("◴", "◷", "◶", "◵")


class BowtieSpinner(Spinner):
    FRAMES: ClassVar[tuple[str, ...]] = (
        "⠋",
        "⠙",
        "⠚",
        "⠞",
        "⠖",
        "⠦",
        "⠴",
        "⠲",
        "⠳",
        "⠓",
    )


class DotWaveSpinner(Spinner):
    FRAMES: ClassVar[tuple[str, ...]] = ("⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷")


class SpinnerType(Enum):
    BRAILLE = "braille"
    LINE = "line"
    CIRCLE = "circle"
    BOWTIE = "bowtie"
    DOT_WAVE = "dot_wave"


_SPINNER_CLASSES: dict[SpinnerType, type[Spinner]] = {
    SpinnerType.BRAILLE: BrailleSpinner,
    SpinnerType.LINE: LineSpinner,
    SpinnerType.CIRCLE: CircleSpinner,
    SpinnerType.BOWTIE: BowtieSpinner,
    SpinnerType.DOT_WAVE: DotWaveSpinner,
}


def create_spinner(spinner_type: SpinnerType = SpinnerType.BRAILLE) -> Spinner:
    spinner_class = _SPINNER_CLASSES.get(spinner_type, BrailleSpinner)
    return spinner_class()
