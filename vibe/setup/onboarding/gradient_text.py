from __future__ import annotations

from rich.text import Text

GRADIENT_COLORS = [
    "#ff6b00",
    "#ff7b00",
    "#ff8c00",
    "#ff9d00",
    "#ffae00",
    "#ffbf00",
    "#ffae00",
    "#ff9d00",
    "#ff8c00",
    "#ff7b00",
]


def gradient_markup(text: str, offset: int) -> str:
    result = []
    for index, char in enumerate(text):
        color = GRADIENT_COLORS[(index + offset) % len(GRADIENT_COLORS)]
        result.append(f"[bold {color}]{char}[/]")
    return "".join(result)


def append_gradient_text(content: Text, text: str, offset: int) -> None:
    for index, char in enumerate(text):
        color = GRADIENT_COLORS[(index + offset) % len(GRADIENT_COLORS)]
        content.append(char, style=f"bold {color}")
