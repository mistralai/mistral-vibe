//! Vim motion targets, as byte offsets into the composer text.

use unicode_width::UnicodeWidthChar;

use crate::utils::graphemes::split_graphemes;
use crate::utils::input_edit::{cursor_left, cursor_right, is_word, line_bounds};

#[derive(Clone, Copy, PartialEq, Eq)]
enum Class {
    Blank,
    Word,
    Punct,
    EmptyLine,
}

/// The graphemes of `input[from..to]` with their offset and class; an empty line is a word of its own.
fn units(input: &str, from: usize, to: usize) -> impl Iterator<Item = (usize, Class)> + '_ {
    split_graphemes(&input[from..to]).map(move |(i, grapheme)| {
        let at = from + i;
        let base = grapheme.chars().find(|c| c.width() != Some(0));
        let class = match base.unwrap_or(' ') {
            '\n' if at == 0 || input[..at].ends_with('\n') => Class::EmptyLine,
            c if c.is_whitespace() => Class::Blank,
            c if is_word(c) => Class::Word,
            _ => Class::Punct,
        };
        (at, class)
    })
}

/// The grapheme start before `cursor`.
pub fn left(input: &str, mut cursor: usize) -> usize {
    cursor_left(input, &mut cursor);
    cursor
}

/// The grapheme end after `cursor`.
pub fn right(input: &str, mut cursor: usize) -> usize {
    cursor_right(input, &mut cursor);
    cursor
}

/// `w`: the start of the next word, crossing lines; the text end when there is none.
pub fn next_word_start(input: &str, cursor: usize) -> usize {
    let mut rest = units(input, cursor, input.len()).peekable();
    if let Some((_, first)) = rest.next_if(|&(_, class)| class != Class::Blank) {
        if first != Class::EmptyLine {
            while rest.next_if(|&(_, class)| class == first).is_some() {}
        }
    }
    rest.find(|&(_, class)| class != Class::Blank)
        .map_or(input.len(), |(i, _)| i)
}

/// `b`: the start of the word before the caret.
pub fn prev_word_start(input: &str, cursor: usize) -> usize {
    let before: Vec<_> = units(input, 0, cursor).collect();
    let mut words = before
        .into_iter()
        .rev()
        .skip_while(|&(_, class)| class == Class::Blank)
        .peekable();
    match words.peek() {
        None => 0,
        Some(&(at, Class::EmptyLine)) => at,
        Some(&(at, first)) => words
            .take_while(|&(_, class)| class == first)
            .last()
            .map_or(at, |(i, _)| i),
    }
}

/// `e`: the last character of the word ending after the caret, else of the text.
pub fn word_end(input: &str, cursor: usize) -> usize {
    let mut rest = units(input, cursor, input.len())
        .skip(1)
        .skip_while(|&(_, class)| matches!(class, Class::Blank | Class::EmptyLine))
        .peekable();
    let Some(&(_, first)) = rest.peek() else {
        return left(input, input.len()).max(cursor);
    };
    rest.take_while(|&(_, class)| class == first)
        .last()
        .map_or(cursor, |(i, _)| i)
}

/// `cw`: the last character of the word under the caret, or `None` on a blank.
pub fn word_end_here(input: &str, cursor: usize) -> Option<usize> {
    let mut rest = units(input, cursor, input.len()).peekable();
    let &(_, first) = rest.peek()?;
    if matches!(first, Class::Blank | Class::EmptyLine) {
        return None;
    }
    rest.take_while(|&(_, class)| class == first)
        .last()
        .map(|(i, _)| i)
}

/// `^`: the first non-blank character of the caret's line.
pub fn first_non_blank(input: &str, cursor: usize) -> usize {
    let (start, end) = line_bounds(input, cursor);
    input[start..end]
        .char_indices()
        .find(|&(_, c)| !c.is_whitespace())
        .map_or(end, |(i, _)| start + i)
}

/// `$`: the last character of the caret's line, where a Normal-mode caret rests.
pub fn last_char(input: &str, cursor: usize) -> usize {
    let (start, end) = line_bounds(input, cursor);
    match end > start {
        true => left(input, end),
        false => start,
    }
}
