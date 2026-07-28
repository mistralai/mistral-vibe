from __future__ import annotations

from collections.abc import Iterable, Sequence
import difflib
from pathlib import Path
import re
from typing import NamedTuple

from textual.color import Color
from textual.content import Content
from textual.geometry import Offset
from textual.highlight import (
    ANSIDarkHighlightTheme,
    ANSILightHighlightTheme,
    HighlightTheme,
    highlight as highlight_code,
)
from textual.selection import Selection
from textual.strip import Strip
from textual.style import Style
from textual.visual import Visual, visualize

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.utils.io import read_safe_async
from vibe.utils.text import line_contexts

_HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

_ADDED_STYLE = "$text-success"
_REMOVED_STYLE = "$text-error"
_MUTED_STYLE = "$text-muted"
_DIM_MUTED_STYLE = "dim $text-muted"

_DIFF_CSS_CLASS_BY_PREFIX: dict[str, str] = {
    "-": "diff-removed",
    "+": "diff-added",
    " ": "diff-context",
}

# Row CSS class → ExpandingBorder color for the matching gutter row.
DIFF_BORDER_COLOR_BY_CLASS: dict[str, str] = {
    "diff-added": "not dim $success",
    "diff-removed": "not dim $error",
}

# Row CSS class → theme variable whose color tints the row band.
_BAND_TINT_BY_CLASS: dict[str, str] = {"diff-added": "success", "diff-removed": "error"}
# Band opacity, matching the old CSS `background: $error 10%` / `20%`.
_BAND_ALPHA_DARK = 0.10
_BAND_ALPHA_LIGHT = 0.20


class DiffOccurrence(NamedTuple):
    # old_lines/new_lines are the changed snippet expanded to whole lines so the
    # diff shows full lines; start_line is None when the location is unknown.
    start_line: int | None
    old_lines: str
    new_lines: str


def language_for_path(file_path: str) -> str:
    """Return the syntax-highlight language guessed from the file extension."""
    return Path(file_path).suffix.lstrip(".") or "text"


async def edit_diff_inputs(
    file_path: str, old_string: str, new_string: str, *, replace_all: bool
) -> list[DiffOccurrence]:
    """One whole-line diff occurrence per match, from a single pre-edit read."""
    path = Path(file_path)
    if not path.is_file():
        return [DiffOccurrence(None, old_string, new_string)]
    content = (await read_safe_async(path)).text
    contexts = line_contexts(content, old_string)
    if not replace_all:
        contexts = contexts[:1]
    if not contexts:
        return [DiffOccurrence(None, old_string, new_string)]
    return [
        DiffOccurrence(
            start, prefix + old_string + suffix, prefix + new_string + suffix
        )
        for start, prefix, suffix in contexts
    ]


def _pick_theme(*, ansi: bool, dark: bool) -> type[HighlightTheme]:
    """Choose the highlight theme matching the ANSI mode and light/dark palette."""
    if not ansi:
        return HighlightTheme
    return ANSIDarkHighlightTheme if dark else ANSILightHighlightTheme


def _highlight_line(code: str, language: str, theme: type[HighlightTheme]) -> Content:
    """Syntax-highlight a single code line, falling back to plain text."""
    lines = highlight_code(code, language=language, theme=theme).split()
    return lines[0] if lines else Content(code)


def _gutter_styles(prefix_char: str, *, ansi: bool) -> tuple[str, str]:
    """Return the (sign, line-number) styles for a diff prefix in the given mode."""
    if prefix_char == "-":
        if ansi:
            return _REMOVED_STYLE, _REMOVED_STYLE
        return _REMOVED_STYLE, _DIM_MUTED_STYLE
    if prefix_char == "+":
        sign_style = _ADDED_STYLE
        return sign_style, _ADDED_STYLE if ansi else _DIM_MUTED_STYLE
    return _MUTED_STYLE, _DIM_MUTED_STYLE


def _build_diff_gutter(prefix_char: str, lineno: int | None, *, ansi: bool) -> Content:
    """Build the styled gutter (line number plus +/-/space sign) for a row."""
    sign_style, lineno_style = _gutter_styles(prefix_char, ansi=ansi)
    lineno_str = f"{lineno:>4} " if lineno is not None else ""
    prefix = f"{prefix_char} "
    return Content.styled(lineno_str, lineno_style) + Content.styled(prefix, sign_style)


def _build_diff_body(
    code: str,
    prefix_char: str,
    language: str,
    *,
    ansi: bool,
    theme: type[HighlightTheme],
) -> Content:
    """Highlight the code body of a row, dimming removed lines in ANSI mode."""
    body = _highlight_line(code, language, theme)
    if prefix_char == "-" and ansi:
        body = body.stylize("dim")
    return body


class _DiffLine(NamedTuple):
    content: Content  # gutter + body, highlighted; no background (that's the band)
    body_plain: str  # body only, for selection/copy
    css_class: str
    gutter_width: int


def _gap_line(*, ansi: bool) -> _DiffLine:
    """Build the ellipsis separator row shown between hunks or occurrences."""
    return _DiffLine(
        Content.styled("⋯", _DIM_MUTED_STYLE if ansi else _MUTED_STYLE),
        "",
        "diff-gap",
        0,
    )


class DiffView(NoMarkupStatic):
    """The whole diff as one line-API widget.

    A single Static holds every diff line as one multi-line ``Content`` so the
    tree stays O(1) instead of three widgets per row. ``render_line`` paints the
    per-row full-width band and ``get_selection`` drops the fixed-width gutter so
    copied text is body-only.

    The band replicates the old per-row ``background: $error 10%`` rule. That is
    a *compositing* instruction, so it must be blended over the real backdrop the
    widget is drawn on (``background_colors[0]``), not flattened over the theme
    background -- otherwise the color drifts wherever the backdrop differs.
    """

    def __init__(self, lines: list[_DiffLine], *, ansi: bool, dark: bool) -> None:
        self._lines = lines
        self._ansi = ansi
        self._dark = dark
        # Uniform within a single render (all rows numbered, or none); gap rows
        # carry width 0 and empty bodies, so max() yields the real gutter width.
        self._gutter_width = max((line.gutter_width for line in lines), default=0)
        # One visual per line so each row can be rendered over its own background;
        # the joined Content on the base Static still drives auto width/height.
        self._visuals = [visualize(self, line.content, markup=False) for line in lines]
        super().__init__(
            Content("\n").join(line.content for line in lines), classes="diff-view"
        )

    def _band_color(self, y: int) -> Color | None:
        """Blend the added/removed tint over the backdrop, or None for plain rows."""
        tint_var = _BAND_TINT_BY_CLASS.get(self._lines[y].css_class)
        # ANSI themes leave the row background untinted (old `&:ansi` rule).
        if tint_var is None or self._ansi:
            return None
        tint = Color.parse(self.app.theme_variables[tint_var])
        alpha = _BAND_ALPHA_DARK if self._dark else _BAND_ALPHA_LIGHT
        return self.background_colors[0].blend(tint, alpha)

    def render_line(self, y: int) -> Strip:
        """Render row y over its own band, applying selection and per-row offsets."""
        width = self.size.width
        if not width or y >= len(self._lines):
            return Strip.blank(width)
        # Paint the real backdrop so auto-contrast foregrounds resolve as before.
        background = self._band_color(y) or self.background_colors[0]
        base = Style(background=background, foreground=self.visual_style.foreground)
        # Single-line visuals bake a row-0 meta offset, so apply selection and
        # per-row offsets ourselves or every row would resolve to row 0.
        strips = Visual.to_strips(
            self, self._row_visual(y), width, 1, base, pad=True, apply_selection=False
        )
        strip = strips[0] if strips else Strip.blank(width)
        return strip.apply_offsets(0, y)

    def _row_visual(self, y: int) -> Visual:
        """Return row y's visual with the selection painted on the body only."""
        selection = self.text_selection
        span = selection.get_span(y) if selection is not None else None
        if span is None:
            return self._visuals[y]
        # The gutter (line numbers + sign) is not part of the copied text, so it
        # must not read as selected either: clamp the highlight to the body.
        start = max(span[0], self._gutter_width)
        end = span[1]
        if end != -1 and end <= start:
            return self._visuals[y]
        style = Style.from_styles(self.screen.get_component_styles("screen--selection"))
        content = self._lines[y].content.stylize(
            style, start, None if end == -1 else end
        )
        return visualize(self, content, markup=False)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Extract the selected text, dropping the gutter so the copy is body-only."""
        text = "\n".join(line.body_plain for line in self._lines)
        gutter = self._gutter_width

        def shift(offset: Offset | None) -> Offset | None:
            if offset is None:
                return None
            return Offset(max(0, offset.x - gutter), offset.y)

        return Selection(shift(selection.start), shift(selection.end)).extract(
            text
        ), "\n"

    @property
    def border_row_colors(self) -> dict[int, str]:
        return diff_border_colors(self._lines)


def render_edit_diff(
    occurrences: Sequence[DiffOccurrence], language: str, *, ansi: bool, dark: bool
) -> list[_DiffLine]:
    """Render all occurrences into diff rows, each anchored at its line number."""
    theme = _pick_theme(ansi=ansi, dark=dark)
    # Each occurrence carries its own whole-line old/new content, so the diff is
    # computed per occurrence and anchored at its line number, with a gap between.
    lines: list[_DiffLine] = []
    for index, occurrence in enumerate(occurrences):
        if index > 0:
            lines.append(_gap_line(ansi=ansi))
        # rstrip only: a trailing newline yields a phantom next-line element to
        # drop, but a leading newline is a real (empty) first line anchored at
        # start_line, so stripping it would desync the gutter line numbers.
        diff_lines = list(
            difflib.unified_diff(
                occurrence.old_lines.rstrip("\n").split("\n"),
                occurrence.new_lines.rstrip("\n").split("\n"),
                lineterm="",
                n=2,
            )
        )[2:]
        lines.extend(
            _render_occurrence(
                diff_lines, occurrence.start_line, language, ansi=ansi, theme=theme
            )
        )
    return lines


def _render_occurrence(
    diff_lines: list[str],
    start_line: int | None,
    language: str,
    *,
    ansi: bool,
    theme: type[HighlightTheme],
) -> list[_DiffLine]:
    """Turn one occurrence's unified-diff lines into numbered, styled diff rows."""
    offset = (start_line - 1) if start_line else 0
    old_lineno = new_lineno = 0  # overwritten by the first @@ header
    lines: list[_DiffLine] = []
    first_hunk = True

    for line in diff_lines:
        prefix_char = line[0]
        code = line[1:]

        if prefix_char == "@":
            # @@ header dropped (gutter has line numbers); gap marks hunk breaks.
            if not first_hunk:
                lines.append(_gap_line(ansi=ansi))
            first_hunk = False
            if match := _HUNK_HEADER_RE.match(line):
                old_lineno = int(match.group(1)) + offset
                new_lineno = int(match.group(2)) + offset
            continue

        if prefix_char == "-":
            lineno = old_lineno
            old_lineno += 1
        elif prefix_char == "+":
            lineno = new_lineno
            new_lineno += 1
        else:
            lineno = new_lineno
            old_lineno += 1
            new_lineno += 1

        lineno_val = lineno if start_line else None
        gutter = _build_diff_gutter(prefix_char, lineno_val, ansi=ansi)
        body = _build_diff_body(code, prefix_char, language, ansi=ansi, theme=theme)

        lines.append(
            _DiffLine(
                gutter + body,
                body.plain,
                _DIFF_CSS_CLASS_BY_PREFIX[prefix_char],
                gutter.cell_length,
            )
        )

    return lines


def diff_border_colors(lines: Iterable[_DiffLine]) -> dict[int, str]:
    """Map each added/removed row index to its gutter border color."""
    return {
        i: DIFF_BORDER_COLOR_BY_CLASS[line.css_class]
        for i, line in enumerate(lines)
        if line.css_class in DIFF_BORDER_COLOR_BY_CLASS
    }
