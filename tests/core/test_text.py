from __future__ import annotations

from vibe.core.utils.text import line_contexts


class TestLineContexts:
    def test_single_occurrence(self) -> None:
        assert line_contexts("foo = bar + baz", "bar") == [(1, "foo = ", " + baz")]

    def test_per_occurrence_distinct_context(self) -> None:
        content = "x = bar + 1\ny = bar - 2\nz = bar\n"
        assert line_contexts(content, "bar") == [
            (1, "x = ", " + 1"),
            (2, "y = ", " - 2"),
            (3, "z = ", ""),
        ]

    def test_snippet_ending_on_newline_has_empty_suffix(self) -> None:
        content = "keep1\nremove\nkeep2\n"
        assert line_contexts(content, "remove\n") == [(2, "", "")]

    def test_leading_newline_anchors_at_match_position(self) -> None:
        # The leading newline belongs to lineA, so the whole-line expansion must
        # include lineA (the line the edit starts modifying) anchored at line 1.
        assert line_contexts("lineA\nlineB", "\nlineB") == [(1, "lineA", "")]

    def test_not_found(self) -> None:
        assert line_contexts("hello\nworld", "missing") == []

    def test_blank_snippet(self) -> None:
        assert line_contexts("hello", "\n") == []
