//! Header text shaping: single-line and multi-line forms plus cell-aware ellipsis.

use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

/// Control/escape bytes a header must never leak to the terminal.
fn is_control(c: char) -> bool {
    matches!(c, '\u{0}'..='\u{1f}' | '\u{7f}')
}

/// Integer with digits grouped in threes by commas.
pub fn thousands(value: u64) -> String {
    let digits = value.to_string();
    let mut grouped = String::with_capacity(digits.len() + digits.len() / 3);
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            grouped.push(',');
        }
        grouped.push(digit);
    }
    grouped
}

/// The first letter upper-cased, the rest as it is.
pub fn capitalize(value: &str) -> String {
    let mut chars = value.chars();
    chars.next().map_or_else(String::new, |first| {
        first.to_uppercase().chain(chars).collect()
    })
}

const COUNT_SUFFIXES: [&str; 5] = ["", "k", "M", "B", "T"];
const SIGNIFICANT_DIGITS: u32 = 3;

/// Three significant digits and a `k`/`M`/`B`/`T` suffix.
pub fn format_compact_count(value: u64) -> String {
    if value < 1_000 {
        return value.to_string();
    }
    let value = u128::from(value);
    let step = 10u128.pow(decimal_digits(value) - SIGNIFICANT_DIGITS);
    let rounded = (value + step / 2) / step * step;
    let group = ((decimal_digits(rounded) - 1) / 3).min(COUNT_SUFFIXES.len() as u32 - 1);
    let unit = 1_000u128.pow(group);
    let fraction = format!("{:03}", rounded % unit * 1_000 / unit);
    let decimals = fraction.trim_end_matches('0');
    let whole = rounded / unit;
    let suffix = COUNT_SUFFIXES[group as usize];
    if decimals.is_empty() {
        format!("{whole}{suffix}")
    } else {
        format!("{whole}.{decimals}{suffix}")
    }
}

/// The compact count, then the exact one as emphasis from one thousand up.
pub fn format_count_markdown(value: u64) -> String {
    let compact = format_compact_count(value);
    if value < 1_000 {
        return compact;
    }
    format!("{compact} _({})_", thousands(value))
}

fn decimal_digits(value: u128) -> u32 {
    value.checked_ilog10().map_or(1, |log| log + 1)
}

/// Flatten every whitespace run to one space so a heredoc keeps the header one row tall.
pub fn single_line(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .filter(|c| !is_control(*c))
        .collect()
}

/// Keep newlines and tabs for the expanded header; normalise CR so it cannot redraw a row.
pub fn multi_line(text: &str) -> String {
    text.replace("\r\n", "\n")
        .replace('\r', "\n")
        .chars()
        .filter(|c| matches!(c, '\n' | '\t') || !is_control(*c))
        .collect()
}

/// The first line of pasted text, splitting on any line boundary.
pub fn first_line(text: &str) -> &str {
    const BREAKS: [char; 10] = [
        '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
        '\u{2029}',
    ];
    text.find(BREAKS).map_or(text, |end| &text[..end])
}

/// Characters of a server error or warning the transcript keeps: a few dozen rows at most.
pub const MESSAGE_MAX_CHARS: usize = 4_000;
pub const MESSAGE_TRUNCATION_MARKER: &str = "… [truncated]";

/// Characters an error row keeps: a bounded message plus room for a short hint after it.
pub const ROW_MAX_CHARS: usize = MESSAGE_MAX_CHARS + 1_000;

/// Cut a server-provided message to `MESSAGE_MAX_CHARS`, marking the cut.
pub fn bounded_message(text: &str) -> std::borrow::Cow<'_, str> {
    bounded(text, MESSAGE_MAX_CHARS)
}

/// Cut a whole error row to `ROW_MAX_CHARS`, marking the cut.
pub fn bounded_row(text: &str) -> std::borrow::Cow<'_, str> {
    bounded(text, ROW_MAX_CHARS)
}

fn bounded(text: &str, max: usize) -> std::borrow::Cow<'_, str> {
    match text.char_indices().nth(max) {
        Some((end, _)) => format!("{}{MESSAGE_TRUNCATION_MARKER}", &text[..end]).into(),
        None => text.into(),
    }
}

/// Cut to `width` cells, spending the last one on `…`, like `text-overflow: ellipsis`.
pub fn ellipsize(text: &str, width: usize) -> String {
    if text.width() <= width {
        return text.to_owned();
    }
    if width == 0 {
        return String::new();
    }
    let mut out = String::new();
    let mut used = 0;
    for grapheme in text.graphemes(true) {
        let cell = grapheme.width();
        if used + cell > width - 1 {
            break;
        }
        out.push_str(grapheme);
        used += cell;
    }
    out.push('…');
    out
}

/// Word-wrap to `width` cells, breaking at newlines like Rich. A run with no
/// break opportunity (a long path) is cut at the cell boundary instead of
/// overflowing its row. This is the only wrap there is: an over-wide row is
/// re-wrapped by whatever paints it, and the overflow lands outside the row's
/// own border or indent.
pub fn wrap_hard(text: &str, width: usize) -> Vec<String> {
    text.split('\n')
        .flat_map(|line| wrap_line(line, width))
        .collect()
}

fn wrap_line(line: &str, width: usize) -> Vec<String> {
    let rows = wrap_ranges(line, width).into_iter();
    rows.map(|range| line[range].to_owned()).collect()
}

/// `line`'s rows at `width` cells as byte ranges into it, as `wrap_hard` folds them; a fold drops the space it breaks at.
pub fn wrap_ranges(line: &str, width: usize) -> Vec<std::ops::Range<usize>> {
    if width == 0 {
        return std::iter::once(0..line.len()).collect();
    }
    let mut rows = Vec::new();
    let (mut start, mut end, mut at) = (0, 0, 0);
    for (index, word) in line.split(' ').enumerate() {
        let (mut word, mut from) = (word, at);
        at += word.len() + 1;
        // Counted per grapheme like `split_at_width`, once, so a long unbroken run folds in linear time.
        let mut cells: usize = word.graphemes(true).map(|grapheme| grapheme.width()).sum();
        while cells > width {
            if end > start {
                rows.push(start..end);
            }
            let (head, tail, used) = split_at_width(word, width);
            rows.push(from..from + head.len());
            (word, from, cells) = (tail, from + head.len(), cells - used);
            (start, end) = (from, from);
        }
        // Only a fold drops the separator, so leading and repeated spaces survive.
        let sep = usize::from(index > 0 && (end > start || rows.is_empty()));
        if end > start && line[start..end].width() + sep + word.width() > width {
            rows.push(start..end);
            start = from;
        } else if end == start {
            start = from - sep;
        }
        end = from + word.len();
    }
    rows.push(start..end);
    rows
}

/// Cut at the last char boundary fitting in `width` cells, always advancing so a
/// glyph wider than the whole row cannot loop forever. Also returns the head's cells.
pub(crate) fn split_at_width(word: &str, width: usize) -> (&str, &str, usize) {
    let mut used = 0;
    for (index, grapheme) in word.grapheme_indices(true) {
        let cell = grapheme.width();
        if index > 0 && used + cell > width {
            let (head, tail) = word.split_at(index);
            return (head, tail, used);
        }
        used += cell;
    }
    (word, "", used)
}

/// Columns between tab stops, as Rich expands a body's tabs.
const TAB_WIDTH: usize = 8;

/// Expand tabs to the next tab stop: a raw tab occupies no cell, so it would
/// leave the cells it should cover unpainted and stale content showing through.
pub fn expand_tabs(line: &str) -> String {
    if !line.contains('\t') {
        return line.to_owned();
    }
    let mut out = String::with_capacity(line.len());
    // The column is counted once per run between tabs, so a long tabbed line expands in linear time.
    let mut column = 0;
    for (index, run) in line.split('\t').enumerate() {
        if index > 0 {
            let pad = TAB_WIDTH - column % TAB_WIDTH;
            out.extend(std::iter::repeat_n(' ', pad));
            column += pad;
        }
        out.push_str(run);
        column += run.width();
    }
    out
}
