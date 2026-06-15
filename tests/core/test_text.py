from __future__ import annotations

from vibe.core.utils.text import snippet_start_line


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
