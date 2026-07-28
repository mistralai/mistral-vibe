from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.geometry import Offset
from textual.highlight import HighlightTheme
from textual.selection import Selection

from vibe.cli.textual_ui.widgets.diff_rendering import (
    DiffOccurrence,
    DiffView,
    _build_diff_body,
    _build_diff_gutter,
    _DiffLine,
    diff_border_colors,
    edit_diff_inputs,
    language_for_path,
    render_edit_diff,
)


def _build(
    code: str, prefix: str, lineno: int | None, language: str, *, ansi: bool
) -> Content:
    return _build_diff_gutter(prefix, lineno, ansi=ansi) + _build_diff_body(
        code, prefix, language, ansi=ansi, theme=HighlightTheme
    )


def _render(old_string, new_string, language, start, *, ansi, dark=True):
    # Tests pass the same old/new at a single start line (int/None) or, for
    # replace_all, at a list of start lines; build one occurrence per location.
    starts = start if isinstance(start, list) else [start]
    occurrences = [DiffOccurrence(s, old_string, new_string) for s in starts]
    return render_edit_diff(occurrences, language, ansi=ansi, dark=dark)


def _render_with_colors(*args, **kwargs):
    lines = _render(*args, **kwargs)
    return lines, diff_border_colors(lines)


def _plain(line: _DiffLine) -> str:
    return line.content.plain


def _styles_at(content: Content, index: int) -> list[str]:
    return [str(s.style) for s in content.spans if s.start <= index < s.end]


class TestLanguageForPath:
    def test_extension(self) -> None:
        assert language_for_path("/src/main.py") == "py"

    def test_no_extension_falls_back_to_text(self) -> None:
        assert language_for_path("/src/Makefile") == "text"


class TestBuildDiffLine:
    def test_line_number_in_content(self) -> None:
        content = _build("x = 1", "+", 42, "py", ansi=False)
        assert "42" in content.plain

    def test_no_line_number(self) -> None:
        content = _build("x = 1", "+", None, "py", ansi=False)
        assert content.plain.startswith("+ ")

    def test_sign_is_colored_in_both_modes(self) -> None:
        for ansi in (False, True):
            content = _build("x = 1", "-", 10, "py", ansi=ansi)
            # "  10 - x = 1": the gutter is 5 chars, so "-" sits at index 5.
            assert any("$text-error" in s for s in _styles_at(content, 5))

    def test_line_number_dimmed_uncolored_in_non_ansi(self) -> None:
        content = _build("x = 1", "-", 10, "py", ansi=False)
        styles = _styles_at(content, 0)
        assert any("dim" in s and "$text-muted" in s for s in styles)
        assert all("$text-error" not in s for s in styles)

    def test_added_line_number_colored_undimmed_in_ansi(self) -> None:
        content = _build("x = 1", "+", 10, "py", ansi=True)
        styles = _styles_at(content, 0)
        assert "$text-success" in styles
        assert all("dim" not in s for s in styles)
        assert all("bold" not in s for s in styles)

    def test_removed_line_number_not_bold_in_ansi(self) -> None:
        content = _build("x = 1", "-", 10, "py", ansi=True)
        lineno_styles = _styles_at(content, 0)
        assert any("$text-error" in s for s in lineno_styles)
        assert all("bold" not in s for s in lineno_styles)
        assert all("dim" not in s for s in lineno_styles)

    def test_removed_sign_not_bold_in_ansi(self) -> None:
        content = _build("x = 1", "-", 10, "py", ansi=True)
        sign_styles = _styles_at(content, 5)
        assert any("$text-error" in s for s in sign_styles)
        assert all("bold" not in s for s in sign_styles)
        assert all("dim" not in s for s in sign_styles)

    def test_line_number_dimmed_for_unchanged_rows_in_ansi(self) -> None:
        content = _build("x = 1", " ", 10, "py", ansi=True)
        styles = _styles_at(content, 0)
        assert any("dim" in s and "$text-muted" in s for s in styles)

    def test_removed_body_dimmed_in_ansi(self) -> None:
        content = _build("foo", "-", 10, "py", ansi=True)
        # body starts after "  10 - " (7 chars)
        assert any("dim" in s for s in _styles_at(content, 7))

    def test_removed_body_not_dimmed_in_non_ansi(self) -> None:
        content = _build("foo", "-", 10, "py", ansi=False)
        assert all("dim" not in s for s in _styles_at(content, 7))


class TestRenderEditDiff:
    def test_simple_replacement(self) -> None:
        lines = _render("x = 100", "x = 200", "py", 1, ansi=False)
        classes = [line.css_class for line in lines]
        assert "diff-removed" in classes
        assert "diff-added" in classes

    def test_no_hunk_header_rendered(self) -> None:
        lines = _render("a\nb\nc\nd\ne\nf", "a\nb\nX\nd\ne\nf", "py", 1, ansi=False)
        for line in lines:
            assert not _plain(line).startswith("@@")

    def test_gap_separator_between_hunks(self) -> None:
        search = "A\nB\nC\nD\nE\nF\nG\nH"
        replace = "Z\nB\nC\nD\nE\nF\nG\nY"
        lines = _render(search, replace, "py", 1, ansi=False)
        assert any(line.css_class == "diff-gap" for line in lines)

    def test_no_leading_gap_for_single_hunk(self) -> None:
        lines = _render("x = 100", "x = 200", "py", 1, ansi=False)
        assert all(line.css_class != "diff-gap" for line in lines)

    def test_pure_insertion(self) -> None:
        lines = _render("x = 1", "x = 1\ny = 2", "py", 1, ansi=False)
        assert any(line.css_class == "diff-added" for line in lines)

    def test_pure_deletion(self) -> None:
        lines = _render("x = 1\ny = 2", "x = 1", "py", 1, ansi=False)
        assert any(line.css_class == "diff-removed" for line in lines)

    def test_line_numbers_use_start_line_offset(self) -> None:
        lines = _render("x = 100", "x = 200", "py", 42, ansi=False)
        assert any("42" in _plain(line) for line in lines)

    @pytest.mark.asyncio
    async def test_leading_newline_snippet_gutter_matches_file_lines(
        self, tmp_path: Path
    ) -> None:
        # A leading-newline snippet starts modifying the previous line; the
        # whole-line diff must show that line and number the hunk against the
        # real file lines, not skip the leading newline and drift by one.
        f = tmp_path / "f.py"
        f.write_text("aa\nbab\ncc\n")
        occurrences = await edit_diff_inputs(
            str(f), "\nbab\nc", "Z\nbab\nC", replace_all=False
        )
        lines = render_edit_diff(occurrences, "py", ansi=False, dark=True)
        file_lines = ["aa", "bab", "cc"]
        for line in lines:
            plain = _plain(line)
            if plain[5:6] in (" ", "-"):
                lineno = int(plain[:4])
                assert plain[7:] == file_lines[lineno - 1]

    def test_multi_hunk_line_numbers(self) -> None:
        search = "A\nB\nC\nD\nE\nF\nG\nH"
        replace = "Z\nB\nC\nD\nE\nF\nG\nY"
        lines = _render(search, replace, "py", 10, ansi=False)
        joined = "\n".join(_plain(line) for line in lines)
        assert "10" in joined
        assert "17" in joined

    def test_no_line_numbers_without_start_line(self) -> None:
        lines = _render("x = 100", "x = 200", "py", None, ansi=False)
        for line in lines:
            plain = _plain(line)
            if plain.startswith(("- ", "+ ")):
                assert not plain[0].isdigit()

    def test_blank_lines_preserved(self) -> None:
        search = "a\n\nb\nc\nd"
        replace = "a\n\nb\nc\nZ"
        lines = _render(search, replace, "py", 1, ansi=False)
        removed = [line for line in lines if line.css_class == "diff-removed"]
        added = [line for line in lines if line.css_class == "diff-added"]
        assert any(_plain(line).rstrip().endswith("d") for line in removed)
        assert any(_plain(line).rstrip().endswith("Z") for line in added)

    def test_replace_all_renders_each_occurrence(self) -> None:
        lines = _render("foo", "bar", "py", [3, 10, 25], ansi=False)
        removed = [line for line in lines if line.css_class == "diff-removed"]
        added = [line for line in lines if line.css_class == "diff-added"]
        assert len(removed) == 3
        assert len(added) == 3

    def test_replace_all_uses_each_start_line(self) -> None:
        lines = _render("foo", "bar", "py", [3, 10, 25], ansi=False)
        joined = "\n".join(_plain(line) for line in lines)
        assert "3" in joined
        assert "10" in joined
        assert "25" in joined

    def test_replace_all_separates_occurrences_with_gap(self) -> None:
        lines = _render("foo", "bar", "py", [3, 10], ansi=False)
        assert sum(line.css_class == "diff-gap" for line in lines) == 1

    def test_single_occurrence_has_no_gap(self) -> None:
        lines = _render("foo", "bar", "py", [3], ansi=False)
        assert all(line.css_class != "diff-gap" for line in lines)

    def test_per_occurrence_full_lines(self) -> None:
        # Each occurrence carries its own whole-line content with its own line.
        lines = render_edit_diff(
            [
                DiffOccurrence(2, "x = bar + 1", "x = qux + 1"),
                DiffOccurrence(7, "y = bar - 2", "y = qux - 2"),
            ],
            "py",
            ansi=False,
            dark=True,
        )
        removed = [_plain(line) for line in lines if line.css_class == "diff-removed"]
        added = [_plain(line) for line in lines if line.css_class == "diff-added"]
        assert any("x = bar + 1" in r for r in removed)
        assert any("y = bar - 2" in r for r in removed)
        assert any("x = qux + 1" in a for a in added)
        assert any("y = qux - 2" in a for a in added)


class TestBorderColors:
    def test_keys_index_into_lines(self) -> None:
        lines, colors = _render_with_colors("x = 100", "x = 200", "py", 1, ansi=False)
        assert colors and all(0 <= k < len(lines) for k in colors)

    def test_added_lines_get_bright_success(self) -> None:
        lines, colors = _render_with_colors("x = 100", "x = 200", "py", 1, ansi=False)
        added = [i for i, line in enumerate(lines) if line.css_class == "diff-added"]
        assert added and all(colors[i] == "not dim $success" for i in added)

    def test_removed_lines_get_bright_error(self) -> None:
        lines, colors = _render_with_colors("x = 100", "x = 200", "py", 1, ansi=False)
        removed = [
            i for i, line in enumerate(lines) if line.css_class == "diff-removed"
        ]
        assert removed and all(colors[i] == "not dim $error" for i in removed)

    def test_context_and_gap_not_in_dict(self) -> None:
        search = "A\nB\nC\nD\nE\nF\nG\nH"
        replace = "Z\nB\nC\nD\nE\nF\nG\nY"
        lines, colors = _render_with_colors(search, replace, "py", 1, ansi=False)
        for i, line in enumerate(lines):
            if line.css_class in ("diff-context", "diff-gap"):
                assert i not in colors

    def test_border_row_colors_property_matches_helper(self) -> None:
        lines = _render("x = 100", "x = 200", "py", 1, ansi=False)
        view = DiffView(lines, ansi=False, dark=True)
        assert view.border_row_colors == diff_border_colors(lines)


class TestDiffViewSelection:
    def test_get_selection_excludes_gutter(self) -> None:
        lines = _render("x = 100", "x = 200", "py", 1, ansi=False)
        view = DiffView(lines, ansi=False, dark=True)
        for y, line in enumerate(lines):
            if line.css_class == "diff-gap":
                continue
            start = Offset(view._gutter_width, y)
            end = Offset(view._gutter_width + len(line.body_plain), y)
            result = view.get_selection(Selection(start, end))
            assert result is not None
            text, ending = result
            assert text == line.body_plain
            assert ending == "\n"

    def test_get_selection_before_gutter_clamps_to_body_start(self) -> None:
        lines = _render("x = 100", "x = 200", "py", 1, ansi=False)
        view = DiffView(lines, ansi=False, dark=True)
        y, line = next(
            (y, line)
            for y, line in enumerate(lines)
            if line.css_class == "diff-removed"
        )
        end = Offset(view._gutter_width + len(line.body_plain), y)
        result = view.get_selection(Selection(Offset(0, y), end))
        assert result is not None
        assert result[0] == line.body_plain

    @pytest.mark.asyncio
    async def test_single_row_selection_does_not_highlight_other_rows(self) -> None:
        lines = _render("a\nb\nc\nd\ne", "a\nB\nc\nD\ne", "py", 1, ansi=False)

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield DiffView(lines, ansi=False, dark=True)

        app = _App()
        async with app.run_test(size=(60, 20)) as pilot:
            view = app.query_one(DiffView)
            unselected = [view.render_line(y) for y in range(len(lines))]

            body = view._gutter_width
            app.screen.selections = {
                view: Selection(Offset(body, 0), Offset(body + 1, 0))
            }
            await pilot.pause()

            selected = [view.render_line(y) for y in range(len(lines))]

        assert selected[0] != unselected[0]
        for y in range(1, len(lines)):
            assert selected[y] == unselected[y]

    @pytest.mark.asyncio
    async def test_render_line_offsets_target_their_own_row(self) -> None:
        lines = _render("a\nb\nc\nd\ne", "a\nB\nc\nD\ne", "py", 1, ansi=False)

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield DiffView(lines, ansi=False, dark=True)

        app = _App()
        async with app.run_test(size=(60, 20)) as pilot:
            view = app.query_one(DiffView)
            await pilot.pause()
            for y in range(len(lines)):
                strip = view.render_line(y)
                offsets_y = {
                    segment.style.meta["offset"][1]
                    for segment in strip
                    if segment.style is not None
                    and segment.style.meta.get("offset") is not None
                }
                assert offsets_y == {y}

    @pytest.mark.asyncio
    async def test_gutter_is_not_highlighted_by_selection(self) -> None:
        lines = _render("x = 100", "x = 200", "py", 1, ansi=False)

        class _App(App[None]):
            def compose(self) -> ComposeResult:
                yield DiffView(lines, ansi=False, dark=True)

        app = _App()
        async with app.run_test(size=(60, 20)) as pilot:
            view = app.query_one(DiffView)
            y = next(
                y for y, line in enumerate(lines) if line.css_class == "diff-removed"
            )
            unselected = view.render_line(y)
            app.screen.selections = {view: Selection(Offset(0, y), Offset(40, y))}
            await pilot.pause()
            selected = view.render_line(y)
            gutter = view._gutter_width
            width = view.size.width

        assert selected.crop(0, gutter) == unselected.crop(0, gutter)
        assert selected.crop(gutter, width) != unselected.crop(gutter, width)
