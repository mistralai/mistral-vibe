from __future__ import annotations

from vibe.core.session.title_format import (
    MAX_TITLE_LENGTH,
    MentionSegment,
    TextSegment,
    format_session_title,
)


class TestFormatSessionTitle:
    def test_empty_returns_empty(self) -> None:
        assert format_session_title([]) == ""

    def test_only_whitespace_returns_empty(self) -> None:
        assert format_session_title([TextSegment(text="   \n\t  ")]) == ""

    def test_plain_text_passthrough(self) -> None:
        assert (
            format_session_title([TextSegment(text="Refactor the auth")])
            == "Refactor the auth"
        )

    def test_mention_no_lines(self) -> None:
        assert format_session_title([MentionSegment(name="foo.py")]) == "@foo.py"

    def test_mention_with_start_line_only(self) -> None:
        assert (
            format_session_title([MentionSegment(name="foo.py", start_line=12)])
            == "@foo.py:12"
        )

    def test_mention_with_line_range(self) -> None:
        assert (
            format_session_title([
                MentionSegment(name="foo.py", start_line=9, end_line=27)
            ])
            == "@foo.py:9-27"
        )

    def test_mixed_text_and_mention(self) -> None:
        segments = [
            TextSegment(text="Refactor "),
            MentionSegment(name="auth.py"),
            TextSegment(text=" please"),
        ]
        assert format_session_title(segments) == "Refactor @auth.py please"

    def test_collapses_whitespace(self) -> None:
        segments = [TextSegment(text="line one\n\nline   two\t\nline three")]
        assert format_session_title(segments) == "line one line two line three"

    def test_strips_outer_whitespace(self) -> None:
        assert (
            format_session_title([TextSegment(text="  hello world  ")]) == "hello world"
        )

    def test_truncates_beyond_max(self) -> None:
        long_text = "a" * (MAX_TITLE_LENGTH + 20)
        result = format_session_title([TextSegment(text=long_text)])
        assert result == "a" * MAX_TITLE_LENGTH + "…"

    def test_no_truncation_at_or_below_max(self) -> None:
        text = "a" * MAX_TITLE_LENGTH
        assert format_session_title([TextSegment(text=text)]) == text

    def test_truncation_can_cut_inside_mention(self) -> None:
        segments = [
            TextSegment(text="x" * (MAX_TITLE_LENGTH - 3)),
            MentionSegment(name="long-filename.py", start_line=5, end_line=10),
        ]
        result = format_session_title(segments)
        assert len(result) == MAX_TITLE_LENGTH + 1
        assert result.endswith("…")
