from __future__ import annotations

from collections.abc import Iterable, Sequence
import difflib
from pathlib import Path
import re
from typing import NamedTuple

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.highlight import (
    ANSIDarkHighlightTheme,
    ANSILightHighlightTheme,
    HighlightTheme,
    highlight as highlight_code,
)
from textual.widget import Widget
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)
from vibe.core.utils.io import read_safe_async
from vibe.core.utils.text import line_contexts

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


class DiffOccurrence(NamedTuple):
    # old_lines/new_lines are the changed snippet expanded to whole lines so the
    # diff shows full lines; start_line is None when the location is unknown.
    start_line: int | None
    old_lines: str
    new_lines: str


def language_for_path(file_path: str) -> str:
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
    if not ansi:
        return HighlightTheme
    return ANSIDarkHighlightTheme if dark else ANSILightHighlightTheme


def _highlight_line(code: str, language: str, theme: type[HighlightTheme]) -> Content:
    lines = highlight_code(code, language=language, theme=theme).split()
    return lines[0] if lines else Content(code)


def _gutter_styles(prefix_char: str, *, ansi: bool) -> tuple[str, str]:
    if prefix_char == "-":
        if ansi:
            return _REMOVED_STYLE, _REMOVED_STYLE
        return _REMOVED_STYLE, _DIM_MUTED_STYLE
    if prefix_char == "+":
        sign_style = _ADDED_STYLE
        return sign_style, _ADDED_STYLE if ansi else _DIM_MUTED_STYLE
    return _MUTED_STYLE, _DIM_MUTED_STYLE


def _build_diff_gutter(prefix_char: str, lineno: int | None, *, ansi: bool) -> Content:
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
    body = _highlight_line(code, language, theme)
    if prefix_char == "-" and ansi:
        body = body.stylize("dim")
    return body


def _build_diff_line(
    code: str,
    prefix_char: str,
    lineno: int | None,
    language: str,
    *,
    ansi: bool,
    theme: type[HighlightTheme],
) -> Content:
    return _build_diff_gutter(prefix_char, lineno, ansi=ansi) + _build_diff_body(
        code, prefix_char, language, ansi=ansi, theme=theme
    )


class _DiffRow(Horizontal):
    def __init__(self, gutter: Content, body: Content, *, classes: str) -> None:
        self._gutter = gutter
        self._body = body
        self.plain = gutter.plain + body.plain
        super().__init__(classes=classes)

    def compose(self) -> ComposeResult:
        yield NonSelectableStatic(self._gutter, classes="diff-gutter")
        yield Static(self._body, classes="diff-body")


def render_edit_diff(
    occurrences: Sequence[DiffOccurrence], language: str, *, ansi: bool, dark: bool
) -> list[Widget]:
    theme = _pick_theme(ansi=ansi, dark=dark)
    # Each occurrence carries its own whole-line old/new content, so the diff is
    # computed per occurrence and anchored at its line number, with a gap between.
    widgets: list[Widget] = []
    for index, occurrence in enumerate(occurrences):
        if index > 0:
            widgets.append(NoMarkupStatic("⋯", classes="diff-gap"))
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
        widgets.extend(
            _render_occurrence(
                diff_lines, occurrence.start_line, language, ansi=ansi, theme=theme
            )
        )
    return widgets


def _render_occurrence(
    diff_lines: list[str],
    start_line: int | None,
    language: str,
    *,
    ansi: bool,
    theme: type[HighlightTheme],
) -> list[Widget]:
    offset = (start_line - 1) if start_line else 0
    old_lineno = new_lineno = 0  # overwritten by the first @@ header
    widgets: list[Widget] = []
    first_hunk = True

    for line in diff_lines:
        prefix_char = line[0]
        code = line[1:]

        if prefix_char == "@":
            # @@ header dropped (gutter has line numbers); gap marks hunk breaks.
            if not first_hunk:
                widgets.append(NoMarkupStatic("⋯", classes="diff-gap"))
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

        widgets.append(
            _DiffRow(gutter, body, classes=_DIFF_CSS_CLASS_BY_PREFIX[prefix_char])
        )

    return widgets


def diff_border_colors(rows: Iterable[Widget]) -> dict[int, str]:
    return {
        i: color
        for i, row in enumerate(rows)
        for cls, color in DIFF_BORDER_COLOR_BY_CLASS.items()
        if cls in row.classes
    }
