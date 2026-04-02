from __future__ import annotations

import pytest

from vibe.cli.textual_ui.widgets.braille_renderer import (
    _braille_char_from_dot_indices,
    _braille_dot_index,
    render_braille,
)


class TestBrailleDotIndex:
    """Tests for _braille_dot_index(x, y)."""

    def test_dot_positions_per_docstring(self) -> None:
        # Layout from docstring: x in {0,1}, y in {0,1,2,3}
        #   -x->  | 1 4  |  2 5  |  3 6  |  V 7 8
        assert _braille_dot_index(0, 0) == 1
        assert _braille_dot_index(1, 0) == 4
        assert _braille_dot_index(0, 1) == 2
        assert _braille_dot_index(1, 1) == 5
        assert _braille_dot_index(0, 2) == 3
        assert _braille_dot_index(1, 2) == 6
        assert _braille_dot_index(0, 3) == 7
        assert _braille_dot_index(1, 3) == 8

    @pytest.mark.parametrize("x", [0, 1])
    @pytest.mark.parametrize("y", [0, 1, 2, 3])
    def test_all_indices_in_range_one_to_eight(self, x: int, y: int) -> None:
        idx = _braille_dot_index(x, y)
        assert 1 <= idx <= 8


class TestBrailleCharFromDotIndices:
    """Tests for _braille_char_from_dot_indices."""

    def test_empty_indices_returns_space(self) -> None:
        assert _braille_char_from_dot_indices([]) == " "

    def test_single_dot_one_returns_braille_char(self) -> None:
        # U+2800 is empty, +1 for dot 1 = U+2801
        char = _braille_char_from_dot_indices([1])
        assert char == "⠁"

    def test_all_dots_returns_full_cell(self) -> None:
        char = _braille_char_from_dot_indices([1, 2, 3, 4, 5, 6, 7, 8])
        # Full block: 0x2800 + (2^8 - 1) = 0x28FF
        assert char == "⣿"

    def test_invalid_index_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid braille dot indices"):
            _braille_char_from_dot_indices([0])

    def test_invalid_index_above_eight_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid braille dot indices"):
            _braille_char_from_dot_indices([9])

    def test_order_of_indices_does_not_matter(self) -> None:
        a = _braille_char_from_dot_indices([1, 2, 3])
        b = _braille_char_from_dot_indices([3, 1, 2])
        assert a == b


class TestRenderBraille:
    """Tests for render_braille(dot_coords, width, height)."""

    def test_empty_coords_produces_blank_grid(self) -> None:
        result = render_braille([], width=4, height=2)
        assert result == "  "

    def test_origin_one_dot(self) -> None:
        # (0, 0) -> first cell, dot 1
        result = render_braille([0], width=2, height=4)
        lines = result.split("\n")
        assert len(lines) == 1
        assert lines[0][0] == "⠁"
        assert lines[0].strip() == "⠁"

    def test_output_dimensions_match_ceiled_width_and_height(self) -> None:
        result = render_braille([], width=3, height=7)
        lines = result.split("\n")
        assert len(lines) == 2
        assert len(lines[0]) == 2

    def test_multiple_dots_in_same_cell_combine(self) -> None:
        # Two dots in first cell: (0,0) and (1,0) -> indices 1 and 4
        result = render_braille([0, 1], width=4, height=4)
        lines = result.split("\n")
        assert len(lines) == 1
        assert lines[0][0] == "⠉"

    def test_dots_in_different_cells(self) -> None:
        # First cell (0,0), second cell (2,0)
        result = render_braille([0, 2], width=4, height=4)
        lines = result.split("\n")
        assert len(lines) == 1
        assert len(lines[0]) == 2
        assert lines[0][0] == "⠁"
        assert lines[0][1] == "⠁"

    def test_multiple_rows(self) -> None:
        # Dot at (0,0) and (0,4) -> two rows
        result = render_braille([0, 4j], width=2, height=8)
        lines = result.split("\n")
        assert len(lines) == 2
        assert lines[0][0] == "⠁"
        assert lines[1][0] == "⠁"

    def test_accepts_complex_coords(self) -> None:
        result = render_braille([1 + 2j], width=4, height=4)
        lines = result.split("\n")
        assert len(lines) == 1
        # x=1,y=2 -> first cell (0,0), sub_x=1, sub_y=2 -> dot index 6
        assert lines[0][0] == _braille_char_from_dot_indices([6])
