from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import functools
from pathlib import Path
import re
from typing import NamedTuple, cast

from rich.align import Align
from rich.color import Color, blend_rgb
from rich.color_triplet import ColorTriplet
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markup import escape
from rich.rule import Rule
from rich.segment import Segment, SegmentLines
from rich.style import Style, StyleType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from unidiff import PatchSet
from unidiff.patch import Hunk, Line, PatchedFile

_hdr_pat = re.compile(r"^@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@$")

MONOKAI_BACKGROUND = Color.from_rgb(red=39, green=40, blue=34)


# get_diff comes from:
# https://github.com/darrenburns/dunk
# license: MIT

# apply_patch comes from:
# https://gist.github.com/noporpoise/16e731849eb1231e86d78f9dfeca3abc
# license: Public domain (CC0)


def get_diff(file_path: str, diff_lines: list[str], width: int) -> list[str]:
    console = Console(width=width)

    with console.capture() as capture:
        input = [f"--- a/{file_path}", f"+++ b/{file_path}"] + diff_lines
        diff = "\n".join(input)
        patch_set: PatchSet = PatchSet(diff)

        for patch in patch_set:
            patch = cast(PatchedFile, patch)
            console.print(PatchedFileHeader(patch))

            source_lineno = 1
            target_lineno = 1

            source_code = Path(file_path).read_text()
            target_code = apply_patch(source_code, diff)
            target_lines = target_code.splitlines(keepends=True)
            source_lineno_max = len(target_lines) - patch.added + patch.removed

            source_hunk_cache: dict[int, Hunk] = {
                hunk.source_start: hunk for hunk in patch
            }
            source_reconstructed: list[str] = []

            while source_lineno <= source_lineno_max:
                hunk = source_hunk_cache.get(source_lineno)
                if hunk:
                    # This line can be reconstructed in source from the hunk
                    lines = [line.value for line in hunk.source_lines()]
                    source_reconstructed.extend(lines)
                    source_lineno += hunk.source_length
                    target_lineno += hunk.target_length
                else:
                    # The line isn't in the diff, pull over current target lines
                    target_line_index = target_lineno - 1

                    line = target_lines[target_line_index]
                    source_reconstructed.append(line)

                    source_lineno += 1
                    target_lineno += 1

            source_code = "".join(source_reconstructed)
            lexer = Syntax.guess_lexer(patch.path)

            for hunk in patch:
                # Use difflib to examine differences between each line of the hunk
                # Target essentially means the additions/green text in diff
                target_line_range = (
                    hunk.target_start,
                    hunk.target_length + hunk.target_start - 1,
                )
                source_line_range = (
                    hunk.source_start,
                    hunk.source_length + hunk.source_start - 1,
                )

                source_syntax = Syntax(
                    source_code,
                    lexer=lexer,
                    line_range=source_line_range,
                    line_numbers=True,
                    indent_guides=True,
                )
                target_syntax = Syntax(
                    target_code,
                    lexer=lexer,
                    line_range=target_line_range,
                    line_numbers=True,
                    indent_guides=True,
                )
                source_removed_linenos = set()
                target_added_linenos = set()

                context_linenos = []
                for line in hunk:
                    line = cast(Line, line)
                    if line.source_line_no and line.is_removed:
                        source_removed_linenos.add(line.source_line_no)
                    elif line.target_line_no and line.is_added:
                        target_added_linenos.add(line.target_line_no)
                    elif line.is_context:
                        context_linenos.append((
                            line.source_line_no,
                            line.target_line_no,
                        ))

                # To ensure that lines are aligned on the left and right in the split
                # diff, we need to add some padding above the lines the amount of padding
                # can be calculated by *changes* in the difference in offset between the
                # source and target context line numbers. When a change occurs, we note
                # how much the change was, and that's how much padding we need to add. If
                # the change in source - target context line numbers is positive,
                # we pad above the target. If it's negative, we pad above the source line.
                source_lineno_to_padding = {}
                target_lineno_to_padding = {}

                first_source_context, first_target_context = next(
                    iter(context_linenos), (0, 0)
                )
                current_delta = first_source_context - first_target_context
                for source_lineno, target_lineno in context_linenos:
                    delta = source_lineno - target_lineno
                    change_in_delta = current_delta - delta
                    pad_amount = abs(change_in_delta)
                    if change_in_delta > 0:
                        source_lineno_to_padding[source_lineno] = pad_amount
                    elif change_in_delta < 0:
                        target_lineno_to_padding[target_lineno] = pad_amount
                    current_delta = delta

                # Track which source and target lines are aligned and should be intraline
                # diffed Work out row number of lines in each side of the diff. Row
                # number is how far from the top of the syntax snippet we are. A line in
                # the source and target with the same row numbers will be aligned in the
                # diff (their line numbers in the source code may be different, though).
                # There can be gaps in row numbers too, since sometimes we add padding
                # above rows to ensure the source and target diffs are aligned with each
                # other.

                # Map row numbers to lines
                source_lines_by_row_index: dict[int, Line] = {}
                target_lines_by_row_index: dict[int, Line] = {}

                # We have to track the length of contiguous streaks of altered lines, as
                # we can only provide intraline diffing to aligned streaks of identical
                # length. If they are different lengths it is almost impossible to align
                # the contiguous streaks without falling back to an expensive heuristic.
                # If a source line and a target line map to equivalent ContiguousStreaks,
                # then we can safely apply intraline highlighting to them.
                source_row_to_contiguous_streak_length: dict[int, ContiguousStreak] = {}

                accumulated_source_padding = 0

                contiguous_streak_row_start = 0
                contiguous_streak_length = 0
                for i, line in enumerate(hunk.source_lines()):
                    if line.is_removed:
                        if contiguous_streak_length == 0:
                            contiguous_streak_row_start = i
                        contiguous_streak_length += 1
                    else:
                        # We've reached the end of the streak, so we'll associate all the
                        # lines in the streak with it for later lookup.
                        for row_index in range(
                            contiguous_streak_row_start,
                            contiguous_streak_row_start + contiguous_streak_length,
                        ):
                            source_row_to_contiguous_streak_length[row_index] = (
                                ContiguousStreak(
                                    streak_row_start=contiguous_streak_row_start,
                                    streak_length=contiguous_streak_length,
                                )
                            )
                        contiguous_streak_length = 0

                    lineno = hunk.source_start + i
                    this_line_padding = source_lineno_to_padding.get(lineno, 0)
                    accumulated_source_padding += this_line_padding
                    row_number = i + accumulated_source_padding
                    source_lines_by_row_index[row_number] = line

                target_row_to_contiguous_streak_length: dict[int, ContiguousStreak] = {}

                accumulated_target_padding = 0

                target_streak_row_start = 0
                target_streak_length = 0
                for i, line in enumerate(hunk.target_lines()):
                    if line.is_added:
                        if target_streak_length == 0:
                            target_streak_row_start = i
                        target_streak_length += 1
                    else:
                        for row_index in range(
                            target_streak_row_start,
                            target_streak_row_start + target_streak_length,
                        ):
                            target_row_to_contiguous_streak_length[row_index] = (
                                ContiguousStreak(
                                    streak_row_start=target_streak_row_start,
                                    streak_length=target_streak_length,
                                )
                            )
                        target_streak_length = 0

                    lineno = hunk.target_start + i
                    this_line_padding = target_lineno_to_padding.get(lineno, 0)
                    accumulated_target_padding += this_line_padding
                    row_number = i + accumulated_target_padding
                    target_lines_by_row_index[row_number] = line

                row_number_to_deletion_ranges = defaultdict(list)
                row_number_to_insertion_ranges = defaultdict(list)

                # Collect intraline diff info for highlighting
                for row_number, source_line in source_lines_by_row_index.items():
                    source_streak = source_row_to_contiguous_streak_length.get(
                        row_number
                    )
                    target_streak = target_row_to_contiguous_streak_length.get(
                        row_number
                    )

                    intraline_enabled = (
                        source_streak is not None
                        and target_streak is not None
                        and source_streak.streak_length == target_streak.streak_length
                    )
                    if not intraline_enabled:
                        continue

                    target_line = target_lines_by_row_index.get(row_number)

                    are_diffable = (
                        source_line
                        and target_line
                        and source_line.is_removed
                        and target_line.is_added
                    )
                    if target_line and are_diffable:
                        matcher = SequenceMatcher(
                            None, source_line.value, target_line.value
                        )
                        opcodes = matcher.get_opcodes()
                        ratio = matcher.ratio()
                        if ratio > 0.5:
                            for tag, i1, i2, j1, j2 in opcodes:
                                if tag == "delete":
                                    row_number_to_deletion_ranges[row_number].append((
                                        i1,
                                        i2,
                                    ))
                                elif tag == "insert":
                                    row_number_to_insertion_ranges[row_number].append((
                                        j1,
                                        j2,
                                    ))
                                elif tag == "replace":
                                    row_number_to_deletion_ranges[row_number].append((
                                        i1,
                                        i2,
                                    ))
                                    row_number_to_insertion_ranges[row_number].append((
                                        j1,
                                        j2,
                                    ))

                source_syntax_lines: list[list[Segment]] = console.render_lines(
                    source_syntax
                )
                target_syntax_lines = console.render_lines(target_syntax)

                highlighted_source_lines = highlight_and_align_lines_in_hunk(
                    console,
                    hunk.source_start,
                    source_removed_linenos,
                    source_syntax_lines,
                    ColorTriplet(255, 0, 0),
                    source_lineno_to_padding,
                    dict(row_number_to_deletion_ranges),
                    gutter_size=len(str(source_lineno_max)) + 2,
                )
                highlighted_target_lines = highlight_and_align_lines_in_hunk(
                    console,
                    hunk.target_start,
                    target_added_linenos,
                    target_syntax_lines,
                    ColorTriplet(0, 255, 0),
                    target_lineno_to_padding,
                    dict(row_number_to_insertion_ranges),
                    gutter_size=len(str(len(target_lines) + 1)) + 2,
                )

                table = Table.grid()
                table.add_column(style="on #0d0f0b")
                table.add_column(style="on #0d0f0b")
                table.add_row(
                    SegmentLines(highlighted_source_lines, new_lines=True),
                    SegmentLines(highlighted_target_lines, new_lines=True),
                )

                hunk_header_style = f"{MONOKAI_BACKGROUND.triplet.hex} on #0d0f0b"
                hunk_header = (
                    f"[on #0d0f0b dim]@@ [red]-{hunk.source_start},{hunk.source_length}[/] "
                    f"[green]+{hunk.target_start},{hunk.target_length}[/] "
                    f"[dim]@@ {hunk.section_header or ''}[/]"
                )
                console.rule(hunk_header, characters="╲", style=hunk_header_style)
                console.print(table)

            console.rule(style="border", characters="▔")

    text = capture.get()
    return text.splitlines()


def highlight_and_align_lines_in_hunk(
    console: Console,
    start_lineno: int,
    highlight_linenos: set[int | None],
    syntax_hunk_lines: list[list[Segment]],
    blend_colour: ColorTriplet,
    lines_to_pad_above: dict[int, int],
    highlight_ranges: dict[int, tuple[int, int]],
    gutter_size: int,
) -> list:
    highlighted_lines = []

    # Apply diff-related highlighting to lines
    for index, line in enumerate(syntax_hunk_lines):
        lineno = index + start_lineno

        if lineno in highlight_linenos:
            new_line = []
            segment_number = 0
            for segment in line:
                style: Style
                text, style, control = segment

                if style:
                    if style.bgcolor:
                        bgcolor_triplet = style.bgcolor.triplet
                        cross_fade = 0.85
                        new_bgcolour_triplet = blend_rgb_cached(
                            blend_colour, bgcolor_triplet, cross_fade=cross_fade
                        )
                        new_bgcolor = Color.from_triplet(new_bgcolour_triplet)
                    else:
                        new_bgcolor = None

                    if style.color and segment_number == 1:
                        new_triplet = blend_rgb_cached(
                            blend_rgb_cached(
                                blend_colour, style.color.triplet, cross_fade=0.5
                            ),
                            ColorTriplet(255, 255, 255),
                            cross_fade=0.4,
                        )
                        new_color = Color.from_triplet(new_triplet)
                    else:
                        new_color = None

                    overlay_style = Style.from_color(
                        color=new_color, bgcolor=new_bgcolor
                    )
                    updated_style = style + overlay_style
                    new_line.append(Segment(text, updated_style, control))
                else:
                    new_line.append(segment)
                segment_number += 1
        else:
            new_line = line[:]

        # Pad above the line if required
        pad = lines_to_pad_above.get(lineno, 0)
        for _ in range(pad):
            highlighted_lines.append([
                Segment("╲" * console.width, Style.from_color(color=MONOKAI_BACKGROUND))
            ])

        # Finally, apply the intraline diff highlighting for this line if possible
        if index in highlight_ranges:
            line_as_text = Text.assemble(
                *((text, style) for text, style, control in new_line), end=""
            )
            intraline_bgcolor = Color.from_triplet(
                blend_rgb_cached(
                    blend_colour, MONOKAI_BACKGROUND.triplet, cross_fade=0.6
                )
            )
            intraline_color = Color.from_triplet(
                blend_rgb_cached(
                    intraline_bgcolor.triplet,
                    Color.from_rgb(255, 255, 255).triplet,
                    cross_fade=0.8,
                )
            )
            for start, end in highlight_ranges.get(index):
                line_as_text.stylize(
                    Style.from_color(color=intraline_color, bgcolor=intraline_bgcolor),
                    start=start + gutter_size + 1,
                    end=end + gutter_size + 1,
                )
            new_line = list(console.render(line_as_text))
        highlighted_lines.append(new_line)
    return highlighted_lines


@functools.lru_cache(maxsize=128)
def blend_rgb_cached(
    colour1: ColorTriplet, colour2: ColorTriplet, cross_fade: float = 0.6
) -> ColorTriplet:
    return blend_rgb(colour1, colour2, cross_fade=cross_fade)


def apply_patch(s: str, patch: str) -> str:
    s = s.splitlines(True)
    p = patch.splitlines(True)
    t = ""
    i = sl = 0
    midx, sign = 1, "+"
    while i < len(p) and p[i].startswith(("---", "+++")):  # skip header lines
        i += 1
    while i < len(p):
        m = _hdr_pat.match(p[i])
        if not m:
            raise Exception(f"Bad patch -- regex mismatch [line {i}]")
        l = int(m.group(midx)) - 1 + (m.group(midx + 1) == "0")
        if sl > l or l > len(s):
            raise Exception(f"Bad patch -- bad line num [line {i}]")
        t += "".join(s[sl:l])
        sl = l
        i += 1
        while i < len(p) and p[i][0] != "@":
            if i + 1 < len(p) and p[i + 1][0] == "\\":
                line = p[i][:-1]
                i += 2
            else:
                line = p[i]
                i += 1
            if len(line) > 0:
                if line[0] == sign or line[0] == " ":
                    t += line[1:]
                sl += line[0] != sign
    t += "".join(s[sl:])
    return t


def simple_pluralise(word: str, number: int) -> str:
    if number == 1:
        return word
    else:
        return word + "s"


@dataclass
class PatchSetHeader:
    file_modifications: int
    file_additions: int
    file_removals: int
    line_additions: int
    line_removals: int

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        if self.file_modifications:
            yield Align.center(
                f"[blue]{self.file_modifications} {simple_pluralise('file', self.file_modifications)} changed"
            )
        if self.file_additions:
            yield Align.center(
                f"[green]{self.file_additions} {simple_pluralise('file', self.file_additions)} added"
            )
        if self.file_removals:
            yield Align.center(
                f"[red]{self.file_removals} {simple_pluralise('file', self.file_removals)} removed"
            )

        bar_width = console.width // 5
        changed_lines = max(1, self.line_additions + self.line_removals)
        added_lines_ratio = self.line_additions / changed_lines

        line_changes_summary = Table.grid()
        line_changes_summary.add_column()
        line_changes_summary.add_column()
        line_changes_summary.add_column()
        line_changes_summary.add_row(
            f"[bold green]+{self.line_additions} ",
            UnderlineBar(
                highlight_range=(0, added_lines_ratio * bar_width),
                highlight_style="green",
                background_style="red",
                width=bar_width,
            ),
            f" [bold red]-{self.line_removals}",
        )

        bar_hpad = len(str(self.line_additions)) + len(str(self.line_removals)) + 4
        yield Align.center(line_changes_summary, width=bar_width + bar_hpad)
        yield Segment.line()


class RemovedFileBody:
    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield Rule(characters="╲", style="hatched")
        yield Rule(" [red]File was removed ", characters="╲", style="hatched")
        yield Rule(characters="╲", style="hatched")
        yield Rule(style="border", characters="▔")


@dataclass
class BinaryFileBody:
    size_in_bytes: int

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield Rule(characters="╲", style="hatched")
        yield Rule(
            Text(f" File is binary · {self.size_in_bytes} bytes ", style="blue"),
            characters="╲",
            style="hatched",
        )
        yield Rule(characters="╲", style="hatched")
        yield Rule(style="border", characters="▔")


class PatchedFileHeader:
    def __init__(self, patch: PatchedFile) -> None:
        self.patch = patch
        if patch.is_rename:
            self.path_prefix = (
                f"[dim][s]{escape(Path(patch.source_file).name)}[/] → [/]"
            )
        elif patch.is_added_file:
            self.path_prefix = "[bold green]Added [/]"
        else:
            self.path_prefix = ""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield Rule(
            f"{self.path_prefix}[b]{escape(self.patch.path)}[/] ([green]{self.patch.added} additions[/], "
            f"[red]{self.patch.removed} removals[/])",
            style="border",
            characters="▁",
        )


class ContiguousStreak(NamedTuple):
    """A single hunk can have multiple streaks of additions/removals of different length"""

    streak_row_start: int
    streak_length: int


class UnderlineBar:
    """Thin horizontal bar with a portion highlighted.

    Args:
        highlight_range (tuple[float, float]): The range to highlight. Defaults to ``(0, 0)`` (no highlight)
        highlight_style (StyleType): The style of the highlighted range of the bar.
        background_style (StyleType): The style of the non-highlighted range(s) of the bar.
        width (int, optional): The width of the bar, or ``None`` to fill available width.
    """

    def __init__(
        self,
        highlight_range: tuple[float, float] = (0, 0),
        highlight_style: StyleType = "magenta",
        background_style: StyleType = "grey37",
        clickable_ranges: dict[str, tuple[int, int]] | None = None,
        width: int | None = None,
    ) -> None:
        self.highlight_range = highlight_range
        self.highlight_style = highlight_style
        self.background_style = background_style
        self.clickable_ranges = clickable_ranges or {}
        self.width = width

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        highlight_style = console.get_style(self.highlight_style)
        background_style = console.get_style(self.background_style)

        half_bar_right = "╸"
        half_bar_left = "╺"
        bar = "━"

        width = self.width or options.max_width
        start, end = self.highlight_range

        start = max(start, 0)
        end = min(end, width)

        output_bar = Text("", end="")

        if start == end == 0 or end < 0 or start > end:
            output_bar.append(Text(bar * width, style=background_style, end=""))
            yield output_bar
            return

        # Round start and end to nearest half
        start = round(start * 2) / 2
        end = round(end * 2) / 2

        # Check if we start/end on a number that rounds to a .5
        half_start = start - int(start) > 0
        half_end = end - int(end) > 0

        # Initial non-highlighted portion of bar
        output_bar.append(
            Text(bar * (int(start - 0.5)), style=background_style, end="")
        )
        if not half_start and start > 0:
            output_bar.append(Text(half_bar_right, style=background_style, end=""))

        # The highlighted portion
        bar_width = int(end) - int(start)
        if half_start:
            output_bar.append(
                Text(
                    half_bar_left + bar * (bar_width - 1), style=highlight_style, end=""
                )
            )
        else:
            output_bar.append(Text(bar * bar_width, style=highlight_style, end=""))
        if half_end:
            output_bar.append(Text(half_bar_right, style=highlight_style, end=""))

        # The non-highlighted tail
        if not half_end and end - width != 0:
            output_bar.append(Text(half_bar_left, style=background_style, end=""))
        output_bar.append(
            Text(bar * (int(width) - int(end) - 1), style=background_style, end="")
        )

        # Fire actions when certain ranges are clicked (e.g. for tabs)
        for range_name, (start, end) in self.clickable_ranges.items():
            output_bar.apply_meta(
                {"@click": f"range_clicked('{range_name}')"}, start, end
            )

        yield output_bar
