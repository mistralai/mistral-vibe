from __future__ import annotations

from collections.abc import Iterable
import difflib
from pathlib import Path
import re

from textual.content import Content
from textual.highlight import (
    ANSIDarkHighlightTheme,
    ANSILightHighlightTheme,
    HighlightTheme,
    highlight as highlight_code,
)
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.utils.io import read_safe
from vibe.core.utils.text import snippet_start_lines

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


def language_for_path(file_path: str) -> str:
    return Path(file_path).suffix.lstrip(".") or "text"


def locate_snippets_in_file(file_path: str, snippet: str) -> list[int]:
    path = Path(file_path)
    if not path.is_file():
        return []
    return snippet_start_lines(read_safe(path).text, snippet)


def _pick_theme(*, ansi: bool, dark: bool) -> type[HighlightTheme]:
    if not ansi:
        return HighlightTheme
    return ANSIDarkHighlightTheme if dark else ANSILightHighlightTheme


def _highlight_line(code: str, language: str, theme: type[HighlightTheme]) -> Content:
    lines = highlight_code(code, language=language, theme=theme).split()
    return lines[0] if lines else Content(code)


def _build_diff_line(
    code: str,
    prefix_char: str,
    lineno: int | None,
    language: str,
    *,
    ansi: bool,
    theme: type[HighlightTheme],
) -> Content:
    # ANSI themes lack row backgrounds; the gutter carries the diff color instead.
    body = _highlight_line(code, language, theme)

    if prefix_char == "-":
        if ansi:
            sign_style = lineno_style = f"bold {_REMOVED_STYLE}"
            body = body.stylize("dim")
        else:
            sign_style, lineno_style = _REMOVED_STYLE, _DIM_MUTED_STYLE
    elif prefix_char == "+":
        sign_style = _ADDED_STYLE
        lineno_style = _ADDED_STYLE if ansi else _DIM_MUTED_STYLE
    else:
        sign_style, lineno_style = _MUTED_STYLE, _DIM_MUTED_STYLE

    lineno_str = f"{lineno:>4} " if lineno is not None else ""
    prefix = f"{prefix_char} "

    return (
        Content.styled(lineno_str, lineno_style)
        + Content.styled(prefix, sign_style)
        + body
    )


def render_edit_diff(
    old_string: str,
    new_string: str,
    language: str,
    start_lines: list[int] | None,
    *,
    ansi: bool,
    dark: bool,
) -> list[Static]:
    theme = _pick_theme(ansi=ansi, dark=dark)
    diff_lines = list(
        difflib.unified_diff(
            old_string.strip("\n").split("\n"),
            new_string.strip("\n").split("\n"),
            lineterm="",
            n=2,
        )
    )[2:]

    # No known locations: render the hunk once without gutter line numbers.
    if not start_lines:
        return _render_occurrence(diff_lines, None, language, ansi=ansi, theme=theme)

    # replace_all repeats the same change at each match; render one block per
    # occurrence, anchored at its own line number, with a gap in between.
    widgets: list[Static] = []
    for index, start_line in enumerate(start_lines):
        if index > 0:
            widgets.append(NoMarkupStatic("⋯", classes="diff-gap"))
        widgets.extend(
            _render_occurrence(diff_lines, start_line, language, ansi=ansi, theme=theme)
        )
    return widgets


def _render_occurrence(
    diff_lines: list[str],
    start_line: int | None,
    language: str,
    *,
    ansi: bool,
    theme: type[HighlightTheme],
) -> list[Static]:
    offset = (start_line - 1) if start_line else 0
    old_lineno = new_lineno = 0  # overwritten by the first @@ header
    widgets: list[Static] = []
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

        content = _build_diff_line(
            code,
            prefix_char,
            lineno if start_line else None,
            language,
            ansi=ansi,
            theme=theme,
        )
        widgets.append(Static(content, classes=_DIFF_CSS_CLASS_BY_PREFIX[prefix_char]))

    return widgets


def diff_border_colors(rows: Iterable[Static]) -> dict[int, str]:
    return {
        i: color
        for i, row in enumerate(rows)
        for cls, color in DIFF_BORDER_COLOR_BY_CLASS.items()
        if cls in row.classes
    }
