from __future__ import annotations

from textual.content import Content
from textual.highlight import HighlightTheme
from textual.widget import Widget

from vibe.cli.textual_ui.widgets.diff_rendering import (
    _build_diff_line,
    diff_border_colors,
    language_for_path,
    render_edit_diff,
)


def _build(
    code: str, prefix: str, lineno: int | None, language: str, *, ansi: bool
) -> Content:
    return _build_diff_line(
        code, prefix, lineno, language, ansi=ansi, theme=HighlightTheme
    )


def _render(*args, **kwargs):
    kwargs.setdefault("dark", True)
    args = list(args)
    # Tests pass a single start line as an int for readability; the renderer
    # expects a list of occurrences.
    if len(args) >= 4 and isinstance(args[3], int):
        args[3] = [args[3]]
    return render_edit_diff(*args, **kwargs)


def _render_with_colors(*args, **kwargs):
    widgets = _render(*args, **kwargs)
    return widgets, diff_border_colors(widgets)


def _plain(widget: Widget) -> str:
    if plain := getattr(widget, "plain", None):
        return plain
    visual = widget.render()
    return visual.plain if isinstance(visual, Content) else str(visual)


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

    def test_removed_line_number_and_sign_bright_in_ansi(self) -> None:
        content = _build("x = 1", "-", 10, "py", ansi=True)
        lineno_styles = _styles_at(content, 0)
        sign_styles = _styles_at(content, 5)
        assert any("bold" in s and "$text-error" in s for s in lineno_styles)
        assert any("bold" in s and "$text-error" in s for s in sign_styles)
        assert all("dim" not in s for s in lineno_styles)
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
        widgets = _render("x = 100", "x = 200", "py", 1, ansi=False)
        classes = [w.classes for w in widgets]
        assert any("diff-removed" in c for c in classes)
        assert any("diff-added" in c for c in classes)

    def test_no_hunk_header_rendered(self) -> None:
        widgets = _render("a\nb\nc\nd\ne\nf", "a\nb\nX\nd\ne\nf", "py", 1, ansi=False)
        for w in widgets:
            assert not _plain(w).startswith("@@")

    def test_gap_separator_between_hunks(self) -> None:
        search = "A\nB\nC\nD\nE\nF\nG\nH"
        replace = "Z\nB\nC\nD\nE\nF\nG\nY"
        widgets = _render(search, replace, "py", 1, ansi=False)
        assert any("diff-gap" in w.classes for w in widgets)

    def test_no_leading_gap_for_single_hunk(self) -> None:
        widgets = _render("x = 100", "x = 200", "py", 1, ansi=False)
        assert all("diff-gap" not in w.classes for w in widgets)

    def test_pure_insertion(self) -> None:
        widgets = _render("x = 1", "x = 1\ny = 2", "py", 1, ansi=False)
        assert any("diff-added" in w.classes for w in widgets)

    def test_pure_deletion(self) -> None:
        widgets = _render("x = 1\ny = 2", "x = 1", "py", 1, ansi=False)
        assert any("diff-removed" in w.classes for w in widgets)

    def test_line_numbers_use_start_line_offset(self) -> None:
        widgets = _render("x = 100", "x = 200", "py", 42, ansi=False)
        assert any("42" in _plain(w) for w in widgets)

    def test_multi_hunk_line_numbers(self) -> None:
        search = "A\nB\nC\nD\nE\nF\nG\nH"
        replace = "Z\nB\nC\nD\nE\nF\nG\nY"
        widgets = _render(search, replace, "py", 10, ansi=False)
        joined = "\n".join(_plain(w) for w in widgets)
        assert "10" in joined
        assert "17" in joined

    def test_no_line_numbers_without_start_line(self) -> None:
        widgets = _render("x = 100", "x = 200", "py", None, ansi=False)
        for w in widgets:
            plain = _plain(w)
            if plain.startswith(("- ", "+ ")):
                assert not plain[0].isdigit()

    def test_blank_lines_preserved(self) -> None:
        search = "a\n\nb\nc\nd"
        replace = "a\n\nb\nc\nZ"
        widgets = _render(search, replace, "py", 1, ansi=False)
        removed = [w for w in widgets if "diff-removed" in w.classes]
        added = [w for w in widgets if "diff-added" in w.classes]
        assert any(_plain(w).rstrip().endswith("d") for w in removed)
        assert any(_plain(w).rstrip().endswith("Z") for w in added)

    def test_replace_all_renders_each_occurrence(self) -> None:
        widgets = render_edit_diff(
            "foo", "bar", "py", [3, 10, 25], ansi=False, dark=True
        )
        removed = [w for w in widgets if "diff-removed" in w.classes]
        added = [w for w in widgets if "diff-added" in w.classes]
        assert len(removed) == 3
        assert len(added) == 3

    def test_replace_all_uses_each_start_line(self) -> None:
        widgets = render_edit_diff(
            "foo", "bar", "py", [3, 10, 25], ansi=False, dark=True
        )
        joined = "\n".join(_plain(w) for w in widgets)
        assert "3" in joined
        assert "10" in joined
        assert "25" in joined

    def test_replace_all_separates_occurrences_with_gap(self) -> None:
        widgets = render_edit_diff("foo", "bar", "py", [3, 10], ansi=False, dark=True)
        assert sum("diff-gap" in w.classes for w in widgets) == 1

    def test_single_occurrence_has_no_gap(self) -> None:
        widgets = render_edit_diff("foo", "bar", "py", [3], ansi=False, dark=True)
        assert all("diff-gap" not in w.classes for w in widgets)


class TestBorderColors:
    def test_keys_index_into_widgets(self) -> None:
        widgets, colors = _render_with_colors("x = 100", "x = 200", "py", 1, ansi=False)
        assert colors and all(0 <= k < len(widgets) for k in colors)

    def test_added_lines_get_bright_success(self) -> None:
        widgets, colors = _render_with_colors("x = 100", "x = 200", "py", 1, ansi=False)
        added_keys = [i for i, w in enumerate(widgets) if "diff-added" in w.classes]
        assert added_keys and all(colors[i] == "not dim $success" for i in added_keys)

    def test_removed_lines_get_bright_error(self) -> None:
        widgets, colors = _render_with_colors("x = 100", "x = 200", "py", 1, ansi=False)
        removed_keys = [i for i, w in enumerate(widgets) if "diff-removed" in w.classes]
        assert removed_keys and all(colors[i] == "not dim $error" for i in removed_keys)

    def test_context_and_gap_not_in_dict(self) -> None:
        search = "A\nB\nC\nD\nE\nF\nG\nH"
        replace = "Z\nB\nC\nD\nE\nF\nG\nY"
        widgets, colors = _render_with_colors(search, replace, "py", 1, ansi=False)
        for i, w in enumerate(widgets):
            if "diff-context" in w.classes or "diff-gap" in w.classes:
                assert i not in colors
