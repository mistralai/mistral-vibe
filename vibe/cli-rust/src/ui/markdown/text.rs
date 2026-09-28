//! Styled-character helpers: left padding, greedy word-wrap, and span merging.

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::Sc;

/// Prefix a run of spans with `pad` columns of left padding.
pub(super) fn pad_line(mut spans: Vec<Span<'static>>, pad: usize) -> Line<'static> {
    let mut all = vec![Span::raw(" ".repeat(pad))];
    all.append(&mut spans);
    Line::from(all)
}

/// Greedy word-wrap styled characters to `width`, like Textual's `divide_line`.
pub(super) fn wrap(chars: &[Sc], width: usize) -> Vec<Vec<Span<'static>>> {
    wrap_chars(chars, width)
        .iter()
        .map(|row| merge(row))
        .collect()
}

/// Greedy word-wrap while preserving styles and terminal-cell widths.
pub(super) fn wrap_chars(chars: &[Sc], width: usize) -> Vec<Vec<Sc>> {
    let width = width.max(1);
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut used = 0;
    for word in split_words(chars) {
        let widths = WordWidths::new(word);
        let trailing_spaces = word.iter().rev().take_while(|&&(c, _)| c == ' ').count();
        let visible = widths.total.saturating_sub(trailing_spaces);
        if !row.is_empty() && used + visible > width {
            rows.push(trim_end(std::mem::take(&mut row)));
            used = 0;
        }
        if visible <= width {
            used += widths.total;
            row.extend_from_slice(word);
            continue;
        }
        let mut start = 0;
        let mut end = 0;
        let mut chunk_width = 0;
        for (next_end, cell) in widths.graphemes {
            if end > start && chunk_width + cell > width {
                rows.push(trim_end(word[start..end].to_vec()));
                start = end;
                chunk_width = 0;
            }
            end = next_end;
            chunk_width += cell;
        }
        if chunk_width > width {
            rows.push(trim_end(word[start..].to_vec()));
        } else {
            used += chunk_width;
            row.extend_from_slice(&word[start..]);
        }
    }
    rows.push(trim_end(row));
    rows
}

/// Split into words, each keeping the spaces that follow it.
fn split_words(chars: &[Sc]) -> Vec<&[Sc]> {
    let mut words = Vec::new();
    let mut start = 0;
    for index in 1..chars.len() {
        if chars[index].0 != ' ' && chars[index - 1].0 == ' ' {
            words.push(&chars[start..index]);
            start = index;
        }
    }
    if start < chars.len() {
        words.push(&chars[start..]);
    }
    words
}

struct WordWidths {
    graphemes: Vec<(usize, usize)>,
    total: usize,
}

impl WordWidths {
    fn new(chars: &[Sc]) -> Self {
        let text = chars
            .iter()
            .map(|(character, _)| character)
            .collect::<String>();
        let mut end = 0;
        let mut total = 0;
        let graphemes = text
            .graphemes(true)
            .map(|grapheme| {
                end += grapheme.chars().count();
                let width = grapheme.width();
                total += width;
                (end, width)
            })
            .collect();
        Self { graphemes, total }
    }
}

pub(super) fn cell_width(chars: &[Sc]) -> usize {
    chars
        .iter()
        .map(|(character, _)| character)
        .collect::<String>()
        .width()
}

fn trim_end(mut chars: Vec<Sc>) -> Vec<Sc> {
    while chars.last().is_some_and(|&(c, _)| c == ' ') {
        chars.pop();
    }
    chars
}

/// Merge consecutive same-style chars into spans.
pub(super) fn merge(chars: &[Sc]) -> Vec<Span<'static>> {
    let mut spans = Vec::new();
    let mut buf = String::new();
    let mut style: Option<Style> = None;
    for &(c, s) in chars {
        if style != Some(s) {
            if let Some(prev) = style {
                spans.push(Span::styled(std::mem::take(&mut buf), prev));
            }
            style = Some(s);
        }
        buf.push(c);
    }
    if let Some(s) = style {
        spans.push(Span::styled(buf, s));
    }
    spans
}
