from __future__ import annotations

from datetime import datetime
import random
from time import time
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.spinner import BrailleSpinner


class LoadingWidget(Static):
    TARGET_COLORS = ("#FFD800", "#FFAF00", "#FF8205", "#FA500F", "#E10500")

    EASTER_EGGS: ClassVar[list[str]] = [
        "Eating a chocolatine",
        "Eating a pain au chocolat",
        "Réflexion",
        "Analyse",
        "Contemplation",
        "Synthèse",
        "Reading Proust",
        "Oui oui baguette",
        "Counting Rs in strawberry",
        "Seeding Mistral weights",
        "Vibing",
        "Sending good vibes",
        "Petting le chat",
    ]

    EASTER_EGGS_HALLOWEEN: ClassVar[list[str]] = [
        "Trick or treating",
        "Carving pumpkins",
        "Summoning spirits",
        "Brewing potions",
        "Haunting the terminal",
        "Petting le chat noir",
    ]

    EASTER_EGGS_DECEMBER: ClassVar[list[str]] = [
        "Wrapping presents",
        "Decorating the tree",
        "Drinking hot chocolate",
        "Building snowmen",
        "Writing holiday cards",
    ]

    def __init__(self, status: str | None = None) -> None:
        super().__init__(classes="loading-widget")
        self.status = status or self._get_default_status()
        self.current_color_index = 0
        self.transition_progress = 0
        self._spinner = BrailleSpinner()
        self.char_widgets: list[Static] = []
        self.spinner_widget: Static | None = None
        self.ellipsis_widget: Static | None = None
        self.hint_widget: Static | None = None
        self.start_time: float | None = None

    def _get_easter_egg(self) -> str | None:
        EASTER_EGG_PROBABILITY = 0.10
        if random.random() < EASTER_EGG_PROBABILITY:
            available_eggs = list(self.EASTER_EGGS)

            OCTOBER = 10
            HALLOWEEN_DAY = 31
            DECEMBER = 12
            now = datetime.now()
            if now.month == OCTOBER and now.day == HALLOWEEN_DAY:
                available_eggs.extend(self.EASTER_EGGS_HALLOWEEN)
            if now.month == DECEMBER:
                available_eggs.extend(self.EASTER_EGGS_DECEMBER)

            return random.choice(available_eggs)
        return None

    def _get_default_status(self) -> str:
        return self._get_easter_egg() or "Generating"

    def _apply_easter_egg(self, status: str) -> str:
        return self._get_easter_egg() or status

    def set_status(self, status: str) -> None:
        self.status = self._apply_easter_egg(status)
        self._rebuild_chars()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="loading-container"):
            self.spinner_widget = Static(
                self._spinner.current_frame(), classes="loading-indicator"
            )
            yield self.spinner_widget

            with Horizontal(classes="loading-status"):
                for char in self.status:
                    widget = Static(char, classes="loading-char")
                    self.char_widgets.append(widget)
                    yield widget

            self.ellipsis_widget = Static("… ", classes="loading-ellipsis")
            yield self.ellipsis_widget

            self.hint_widget = Static("(0s esc to interrupt)", classes="loading-hint")
            yield self.hint_widget

    def _rebuild_chars(self) -> None:
        if not self.is_mounted:
            return

        status_container = self.query_one(".loading-status", Horizontal)

        status_container.remove_children()
        self.char_widgets.clear()

        for char in self.status:
            widget = Static(char, classes="loading-char")
            self.char_widgets.append(widget)
            status_container.mount(widget)

        self.update_animation()

    def on_mount(self) -> None:
        self.start_time = time()
        self.update_animation()
        self.set_interval(0.1, self.update_animation)

    def _get_color_for_position(self, position: int) -> str:
        current_color = self.TARGET_COLORS[self.current_color_index]
        next_color = self.TARGET_COLORS[
            (self.current_color_index + 1) % len(self.TARGET_COLORS)
        ]
        if position < self.transition_progress:
            return next_color
        return current_color

    def update_animation(self) -> None:
        total_elements = 1 + len(self.char_widgets) + 2

        if self.spinner_widget:
            spinner_char = self._spinner.next_frame()
            color = self._get_color_for_position(0)
            self.spinner_widget.update(f"[{color}]{spinner_char}[/]")

        for i, widget in enumerate(self.char_widgets):
            position = 1 + i
            color = self._get_color_for_position(position)
            widget.update(f"[{color}]{self.status[i]}[/]")

        if self.ellipsis_widget:
            ellipsis_start = 1 + len(self.status)
            color_ellipsis = self._get_color_for_position(ellipsis_start)
            color_space = self._get_color_for_position(ellipsis_start + 1)
            self.ellipsis_widget.update(f"[{color_ellipsis}]…[/][{color_space}] [/]")

        self.transition_progress += 1
        if self.transition_progress > total_elements:
            self.current_color_index = (self.current_color_index + 1) % len(
                self.TARGET_COLORS
            )
            self.transition_progress = 0

        if self.hint_widget and self.start_time is not None:
            elapsed = int(time() - self.start_time)
            self.hint_widget.update(f"({elapsed}s esc to interrupt)")
