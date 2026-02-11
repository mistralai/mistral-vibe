from __future__ import annotations

from collections.abc import Iterable
import math

# for more details on braille characters encoding, see: https://en.wikipedia.org/wiki/Braille_Patterns

_BRAILLE_DOT_COUNT = 8


def _braille_dot_index(x: int, y: int) -> int:
    """returns the number associated with a dot in a braille character
    x ∈ {0, 1}, y ∈ {0, 1, 2, 3}
     -x->
    | 1 4
    y 2 5
    | 3 6
    V 7 8
    """
    if y < 3:  # noqa: PLR2004
        return y + 1 + 3 * x
    return 7 + x


def _braille_char_from_dot_indices(indices: list[int]) -> str:
    if any(n < 1 or n > _BRAILLE_DOT_COUNT for n in indices):
        raise ValueError(f"Invalid braille dot indices: {indices}")
    return chr(0x2800 + sum(2 ** (d - 1) for d in indices)) if indices else " "


def render_braille(dot_coords: Iterable[complex], width: int, height: int) -> str:
    """this function receives a list of dot coordinantes, a width and a height,
    and returns a string representing these dots with braille characters.

    Origin is (0,0) and is located at the top left:
    0----x---->
    |
    y
    |
    V
    """
    dots_matrix: list[list[list[int]]] = [
        [[] for _ in range(math.ceil(width / 2))] for _ in range(math.ceil(height / 4))
    ]  # the list of dots for each character in the final str

    for coord in dot_coords:
        x = int(coord.real // 2)
        y = int(coord.imag // 4)
        sub_x = int(coord.real) % 2
        sub_y = int(coord.imag) % 4
        dots_matrix[y][x].append(_braille_dot_index(sub_x, sub_y))

    braille_chars = [
        [_braille_char_from_dot_indices(char_dots) for char_dots in row]
        for row in dots_matrix
    ]

    return "\n".join("".join(row) for row in braille_chars)
