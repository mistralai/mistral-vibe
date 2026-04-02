from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from enum import Enum, auto
import random
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from textual.timer import Timer

from vibe.cli.textual_ui.widgets.braille_renderer import render_braille

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


class PulseSpinner(Spinner):
    FRAMES: ClassVar[tuple[str, ...]] = (
        "■",
        "■",
        "■",
        "■",
        "■",
        "■",
        "□",
        "□",
        "□",
        "□",
    )


class SpinnerType(Enum):
    BRAILLE = auto()
    PULSE = auto()
    SNAKE = auto()


class SnakeSpinner(Spinner):
    MAP_WIDTH: ClassVar[int] = 4
    MAP_HEIGHT: ClassVar[int] = 4
    SNAKE_LENGTH: ClassVar[int] = 3

    def __init__(self) -> None:
        self._positions: list[complex] = [1, 0, 1j]
        super().__init__()

    @property
    def current_direction(self) -> complex:
        return self._positions[0] - self._positions[1]

    def _is_in_bounds(self, position: complex) -> bool:
        return (
            0 <= position.real < self.MAP_WIDTH and 0 <= position.imag < self.MAP_HEIGHT
        )

    def _get_direction(self) -> complex:
        if (
            len(set(z.real for z in self._positions)) > 1
            and len(set(z.imag for z in self._positions)) > 1
            and self._is_in_bounds(self._positions[0] + self.current_direction)
        ):
            return self.current_direction
        valid_directions = []
        for rotation in [1, 1j, -1j]:
            offset = rotation * self.current_direction
            new_position = self._positions[0] + offset
            if self._is_in_bounds(new_position) and new_position not in self._positions:
                valid_directions.append(offset)
        return random.choice(valid_directions)

    def _next_positions(self) -> list[complex]:
        if len(self._positions) > self.SNAKE_LENGTH:
            return self._positions[: self.SNAKE_LENGTH]
        head_position = self._positions[0]
        direction = self._get_direction()
        if self.current_direction != direction:
            return [head_position + direction] + self._positions
        return [head_position + direction] + self._positions[:-1]

    def current_frame(self) -> str:
        return render_braille(self._positions, self.MAP_WIDTH, self.MAP_HEIGHT)

    def next_frame(self) -> str:
        self._positions = self._next_positions()
        return self.current_frame()

    def reset(self) -> None:
        self._positions = [1, 0, 1j]


_SPINNER_CLASSES: dict[SpinnerType, type[Spinner]] = {
    SpinnerType.BRAILLE: BrailleSpinner,
    SpinnerType.PULSE: PulseSpinner,
    SpinnerType.SNAKE: SnakeSpinner,
}


def create_spinner(spinner_type: SpinnerType = SpinnerType.BRAILLE) -> Spinner:
    spinner_class = _SPINNER_CLASSES.get(spinner_type, BrailleSpinner)
    return spinner_class()


class SpinnerMixin:
    SPINNER_TYPE: ClassVar[SpinnerType] = SpinnerType.BRAILLE
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
