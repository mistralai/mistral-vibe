//! Text mechanics for the chat input: caret moves, deletes, word/line
//! boundaries. Pure operations on `(input, cursor)`; the `chat input` module owns
//! the `Action`/selection policy on top.

use unicode_segmentation::UnicodeSegmentation;

/// A word char, matching Python's `\w` (unicode alphanumerics plus underscore).
pub(crate) fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Byte range of the `\w+` run around `cursor`, Python `_word_boundary_around`.
/// `None` when the cursor sits between two non-word chars.
pub(crate) fn word_bounds(input: &str, cursor: usize) -> Option<(usize, usize)> {
    let (line_start, line_end) = line_bounds(input, cursor);
    let cursor = cursor.clamp(line_start, line_end);
    let start = input[line_start..cursor]
        .char_indices()
        .rev()
        .take_while(|(_, c)| is_word(*c))
        .last()
        .map_or(cursor, |(b, _)| line_start + b);
    let end = input[cursor..line_end]
        .char_indices()
        .find(|(_, c)| !is_word(*c))
        .map_or(line_end, |(b, _)| cursor + b);
    (start < end).then_some((start, end))
}

/// Byte range `[start, end)` of the document line containing `cursor`.
pub(crate) fn line_bounds(input: &str, cursor: usize) -> (usize, usize) {
    let start = input[..cursor].rfind('\n').map(|i| i + 1).unwrap_or(0);
    let end = input[cursor..]
        .find('\n')
        .map(|i| cursor + i)
        .unwrap_or(input.len());
    (start, end)
}

/// Byte length of the grapheme starting at `cursor`, or 0 at end of text.
fn grapheme_len_at(input: &str, cursor: usize) -> usize {
    input[cursor..].graphemes(true).next().map_or(0, str::len)
}

/// Byte length of the grapheme ending at `cursor`, or 0 at start of text.
fn grapheme_len_before(input: &str, cursor: usize) -> usize {
    input[..cursor]
        .graphemes(true)
        .next_back()
        .map_or(0, str::len)
}

/// Insert `s` at the cursor and advance past it (Textual paste / typed char).
pub(crate) fn insert(input: &mut String, cursor: &mut usize, s: &str) {
    input.insert_str(*cursor, s);
    *cursor += s.len();
}

/// Delete the grapheme left of the cursor (`backspace`).
pub(crate) fn delete_left(input: &mut String, cursor: &mut usize) {
    let n = grapheme_len_before(input, *cursor);
    if n > 0 {
        input.replace_range(*cursor - n..*cursor, "");
        *cursor -= n;
    }
}

/// Delete the grapheme right of the cursor (`delete`, `ctrl+d` with text).
pub(crate) fn delete_right(input: &mut String, cursor: &usize) {
    let n = grapheme_len_at(input, *cursor);
    if n > 0 {
        input.replace_range(*cursor..*cursor + n, "");
    }
}

/// Move the cursor one grapheme left (`left`).
pub(crate) fn cursor_left(input: &str, cursor: &mut usize) {
    *cursor -= grapheme_len_before(input, *cursor);
}

/// Move the cursor one grapheme right (`right`).
pub(crate) fn cursor_right(input: &str, cursor: &mut usize) {
    *cursor += grapheme_len_at(input, *cursor);
}

/// Column (byte offset within its line) the cursor jumps to going one word left.
fn word_left_target(input: &str, cursor: usize) -> usize {
    let (start, _) = line_bounds(input, cursor);
    if cursor == start {
        // At column 0: jump to the end of the previous line (the '\n' before us).
        return if start > 0 { start - 1 } else { 0 };
    }
    let prefix = input[start..cursor].trim_end();
    // The last \W|\w boundary in the prefix, else the line start.
    let chars: Vec<char> = prefix.chars().collect();
    let mut col = 0;
    for i in 1..chars.len() {
        if is_word(chars[i - 1]) != is_word(chars[i]) {
            col = i;
        }
    }
    start
        + prefix
            .char_indices()
            .nth(col)
            .map_or(prefix.len(), |(b, _)| b)
}

/// Column (byte offset in `input`) the cursor jumps to going one word right.
fn word_right_target(input: &str, cursor: usize) -> usize {
    let (_, end) = line_bounds(input, cursor);
    if cursor == end {
        // At line end: jump to the start of the next line if there is one.
        return if end < input.len() { end + 1 } else { end };
    }
    let suffix = &input[cursor..end];
    let trimmed = suffix.trim_start();
    let strip = suffix.len() - trimmed.len();
    // First \w|\W boundary in the whitespace-stripped suffix, else the line end.
    let chars: Vec<char> = trimmed.chars().collect();
    for i in 1..chars.len() {
        if is_word(chars[i - 1]) != is_word(chars[i]) {
            let byte = trimmed
                .char_indices()
                .nth(i)
                .map_or(trimmed.len(), |(b, _)| b);
            return cursor + strip + byte;
        }
    }
    end
}

/// Move the cursor one word left (`ctrl+left`, `alt+left`).
pub(crate) fn cursor_word_left(input: &str, cursor: &mut usize) {
    *cursor = word_left_target(input, *cursor);
}

/// Move the cursor one word right (`ctrl+right`, `alt+right`).
pub(crate) fn cursor_word_right(input: &str, cursor: &mut usize) {
    *cursor = word_right_target(input, *cursor);
}

/// Move the cursor to the start of the current line, smart-home (`home`, `ctrl+a`).
pub(crate) fn cursor_line_start(input: &str, cursor: &mut usize) {
    let (start, end) = line_bounds(input, *cursor);
    let first_non_ws = input[start..end]
        .char_indices()
        .find(|(_, c)| !c.is_whitespace())
        .map_or(0, |(b, _)| b);
    let target = start + first_non_ws;
    *cursor = if *cursor == start || *cursor > target {
        target
    } else {
        start
    };
}

/// Move the cursor to the end of the current line (`end`, `ctrl+e`).
pub(crate) fn cursor_line_end(input: &str, cursor: &mut usize) {
    *cursor = line_bounds(input, *cursor).1;
}

/// Delete the word left of the cursor (`ctrl+w`, `alt+backspace`, `ctrl+backspace`).
pub(crate) fn delete_word_left(input: &mut String, cursor: &mut usize) {
    if *cursor == 0 {
        return;
    }
    let target = word_left_target(input, *cursor);
    input.replace_range(target..*cursor, "");
    *cursor = target;
}

/// Delete the word right of the cursor (`alt+delete`).
pub(crate) fn delete_word_right(input: &mut String, cursor: &usize) {
    if *cursor == input.len() {
        return;
    }
    let (_, end) = line_bounds(input, *cursor);
    let suffix = &input[*cursor..end];
    // Delete up to the end of the next word (leading non-word chars included).
    let word_end = suffix
        .char_indices()
        .find(|(_, c)| is_word(*c))
        .map(|(fw, _)| {
            *cursor
                + suffix[fw..]
                    .char_indices()
                    .find(|(_, c)| !is_word(*c))
                    .map_or(suffix.len(), |(b, _)| fw + b)
        });
    let to = match word_end {
        Some(t) => t,
        None if *cursor == end && end < input.len() => end + 1,
        None => end,
    };
    input.replace_range(*cursor..to, "");
}

/// Delete from the cursor to the start of the line (`ctrl+u`).
pub(crate) fn delete_to_start_of_line(input: &mut String, cursor: &mut usize) {
    let (start, _) = line_bounds(input, *cursor);
    if *cursor == start {
        // At column 0: delete the preceding '\n', joining with the line above.
        delete_left(input, cursor);
    } else {
        input.replace_range(start..*cursor, "");
        *cursor = start;
    }
}

/// Delete to the end of the line, or delete an empty line (`ctrl+k`).
pub(crate) fn delete_to_end_of_line_or_delete_line(input: &mut String, cursor: &mut usize) {
    let (start, end) = line_bounds(input, *cursor);
    if start == end {
        // Empty line: drop the line and its terminator.
        let to = if end < input.len() { end + 1 } else { end };
        input.replace_range(start..to, "");
        *cursor = start.min(input.len());
    } else if *cursor == end {
        // At line end: pull the next line up (delete the terminating '\n').
        delete_right(input, cursor);
    } else {
        input.replace_range(*cursor..end, "");
    }
}
