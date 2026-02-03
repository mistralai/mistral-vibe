from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from textual.timer import Timer

if TYPE_CHECKING:
    from textual.widgets import Static


@runtime_checkable
class HasSetInterval(Protocol):
    def set_interval(
        self, interval: float, callback: Callable[[], None], *, name: str | None = None
    ) -> Timer: ...


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


class SpinnerMixin:
    SPINNER_TYPE: ClassVar[SpinnerType] = SpinnerType.LINE
    SPINNING_TEXT: ClassVar[str] = ""
    COMPLETED_TEXT: ClassVar[str] = ""

    _spinner: Spinner
    _spinner_timer: Any
    _is_spinning: bool
    _indicator_widget: Static | None
    _status_text_widget: Static | None

    def init_spinner(self) -> None:
        self._spinner = create_spinner(self.SPINNER_TYPE)
        self._spinner_timer = None
        self._is_spinning = True
        self._status_text_widget = None

    def start_spinner_timer(self) -> None:
        if not isinstance(self, HasSetInterval):
            raise TypeError(
                "SpinnerMixin requires a class that implements HasSetInterval protocol"
            )
        self._spinner_timer = self.set_interval(0.1, self._update_spinner_frame)

    def _update_spinner_frame(self) -> None:
        if not self._is_spinning or not self._indicator_widget:
            return
        self._indicator_widget.update(self._spinner.next_frame())

    def refresh_spinner(self) -> None:
        if self._indicator_widget:
            self._indicator_widget.refresh()

    def stop_spinning(self, success: bool = True) -> None:
        self._is_spinning = False
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        if self._indicator_widget:
            if success:
                self._indicator_widget.update("✓")
                self._indicator_widget.add_class("success")
            else:
                self._indicator_widget.update("✕")
                self._indicator_widget.add_class("error")
        if self._status_text_widget and self.COMPLETED_TEXT:
            self._status_text_widget.update(self.COMPLETED_TEXT)

    def on_unmount(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
