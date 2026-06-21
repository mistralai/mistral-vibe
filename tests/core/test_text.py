from __future__ import annotations

from vibe.core.utils.text import snippet_start_line, snippet_start_lines


class TestSnippetStartLine:
    def test_finds_line_number(self) -> None:
        assert snippet_start_line("a\nb\nc\nd\n", "c") == 3

    def test_first_line(self) -> None:
        assert snippet_start_line("hello\nworld", "hello") == 1

    def test_multiline_snippet(self) -> None:
        assert snippet_start_line("a\nb\nc", "\nb\n") == 2

    def test_first_occurrence_when_repeated(self) -> None:
        assert snippet_start_line("x\nx\nx", "x") == 1

    def test_leading_newline_anchors_first_content_line(self) -> None:
        assert snippet_start_line("bar\nx\nbar", "\nbar") == 3

    def test_returns_none_when_exact_snippet_absent(self) -> None:
        assert snippet_start_line("a\nb\nfoo", "foo\n") is None

    def test_not_found(self) -> None:
        assert snippet_start_line("hello\nworld", "missing") is None

    def test_blank_snippet(self) -> None:
        assert snippet_start_line("hello", "\n") is None


class TestSnippetStartLines:
    def test_single_occurrence(self) -> None:
        assert snippet_start_lines("a\nb\nc", "b") == [2]

    def test_all_occurrences(self) -> None:
        assert snippet_start_lines("x\ny\nx\nz\nx", "x") == [1, 3, 5]

    def test_repeated_on_same_line(self) -> None:
        assert snippet_start_lines("x x x", "x") == [1, 1, 1]

    def test_non_overlapping(self) -> None:
        assert snippet_start_lines("aaaa", "aa") == [1, 1]

    def test_multiline_snippet_occurrences(self) -> None:
        assert snippet_start_lines("a\nb\nc\na\nb", "a\nb") == [1, 4]

    def test_not_found(self) -> None:
        assert snippet_start_lines("hello\nworld", "missing") == []

    def test_blank_snippet(self) -> None:
        assert snippet_start_lines("hello", "\n") == []
