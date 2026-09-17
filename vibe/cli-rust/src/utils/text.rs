//! Header text shaping: Python's `_single_line` / `_multi_line` plus cell-aware ellipsis.

use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

/// Control/escape bytes a header must never leak to the terminal.
fn is_control(c: char) -> bool {
    matches!(c, '\u{0}'..='\u{1f}' | '\u{7f}')
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

/// Strip terminal control sequences and collapse carriage-return redraws.
pub fn clean_output(text: &str) -> String {
    text.replace("\r\n", "\n")
        .split('\n')
        .map(|line| {
            let written = line.trim_end_matches('\r');
            let visible = written
                .rsplit_once('\r')
                .map(|(_, tail)| tail)
                .unwrap_or(written);
            strip_terminal_sequences(visible)
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn strip_terminal_sequences(text: &str) -> String {
    let mut chars = text.chars().peekable();
    let mut output = String::new();
    while let Some(character) = chars.next() {
        match character {
            '\u{1b}' => strip_escape_sequence(&mut chars),
            '\u{009b}' => strip_control_sequence(&mut chars),
            '\u{0090}' | '\u{0098}' | '\u{009d}' | '\u{009e}' | '\u{009f}' => {
                strip_string_sequence(&mut chars)
            }
            '\t' => output.push('\t'),
            character if character.is_control() => {}
            character => output.push(character),
        }
    }
    output
}

fn strip_escape_sequence(chars: &mut std::iter::Peekable<impl Iterator<Item = char>>) {
    match chars.next() {
        Some('[') => strip_control_sequence(chars),
        Some(']' | 'P' | 'X' | '^' | '_') => strip_string_sequence(chars),
        Some(character) if ('\u{20}'..='\u{2f}').contains(&character) => {
            while chars
                .next_if(|next| ('\u{20}'..='\u{2f}').contains(next))
                .is_some()
            {}
            let _ = chars.next();
        }
        Some(_) | None => {}
    }
}

fn strip_control_sequence(chars: &mut impl Iterator<Item = char>) {
    for character in chars {
        if ('@'..='~').contains(&character) {
            break;
        }
    }
}

fn strip_string_sequence(chars: &mut std::iter::Peekable<impl Iterator<Item = char>>) {
    while let Some(character) = chars.next() {
        if matches!(character, '\u{7}' | '\u{009c}') {
            break;
        }
        if character == '\u{1b}' && chars.next_if_eq(&'\\').is_some() {
            break;
        }
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
/// overflowing its row, as Textual's `text-overflow: fold` does. This is the
/// only wrap there is: an over-wide row is re-wrapped by whatever paints it,
/// and the overflow lands outside the row's own border or indent.
pub fn wrap_hard(text: &str, width: usize) -> Vec<String> {
    text.split('\n')
        .flat_map(|line| wrap_line(line, width))
        .collect()
}

fn wrap_line(line: &str, width: usize) -> Vec<String> {
    if width == 0 {
        return vec![line.to_owned()];
    }
    let mut rows: Vec<String> = Vec::new();
    let mut row = String::new();
    for (index, word) in line.split(' ').enumerate() {
        let mut word = word;
        while word.width() > width {
            if !row.is_empty() {
                rows.push(std::mem::take(&mut row));
            }
            let (head, tail) = split_at_width(word, width);
            rows.push(head.to_owned());
            word = tail;
        }
        // Only a fold drops the separator, so leading and repeated spaces survive.
        let sep = usize::from(index > 0 && (!row.is_empty() || rows.is_empty()));
        if !row.is_empty() && row.width() + sep + word.width() > width {
            rows.push(std::mem::take(&mut row));
        } else if sep == 1 {
            row.push(' ');
        }
        row.push_str(word);
    }
    rows.push(row);
    rows
}

/// Cut at the last char boundary fitting in `width` cells, always advancing so a
/// glyph wider than the whole row cannot loop forever.
fn split_at_width(word: &str, width: usize) -> (&str, &str) {
    let mut used = 0;
    for (index, grapheme) in word.grapheme_indices(true) {
        let cell = grapheme.width();
        if index > 0 && used + cell > width {
            return word.split_at(index);
        }
        used += cell;
    }
    (word, "")
}

/// Columns between tab stops, as Rich expands a body's tabs.
const TAB_WIDTH: usize = 8;

/// Expand tabs to the next tab stop: a raw tab occupies no cell, so it would
/// leave the cells it should cover unpainted and stale content showing through.
pub fn expand_tabs(line: &str) -> String {
    if !line.contains('\t') {
        return line.to_owned();
    }
    let mut out = String::new();
    for character in line.chars() {
        match character {
            '\t' => {
                let pad = TAB_WIDTH - out.width() % TAB_WIDTH;
                out.extend(std::iter::repeat_n(' ', pad));
            }
            _ => out.push(character),
        }
    }
    out
}
