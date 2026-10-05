//! Styled-character helpers: left padding, greedy word-wrap, and span merging.

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_segmentation::UnicodeSegmentation;
use unicode_width::UnicodeWidthStr;

use super::Sc;

/// `pad` columns of left padding.
pub(super) fn pad(pad: usize) -> Span<'static> {
    Span::raw(" ".repeat(pad))
}

/// Prefix a run of spans with `pad` columns of left padding.
pub(super) fn pad_line(mut spans: Vec<Span<'static>>, pad: usize) -> Line<'static> {
    let mut all = vec![self::pad(pad)];
    all.append(&mut spans);
    Line::from(all)
}

/// Greedy word-wrap styled characters to `width`, like Textual's `divide_line`.
pub(crate) fn wrap_chars(chars: &[Sc], width: usize) -> Vec<Vec<Sc>> {
    let width = width.max(1);
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut used = 0;
    for word in split_words(chars) {
        if word.len() == 1 && word[0].0 == '\n' {
            rows.push(trim_end(std::mem::take(&mut row)));
            used = 0;
            continue;
        }
        let widths = WordWidths::new(word);
        let trailing_spaces = word.iter().rev().take_while(|sc| sc.0 == ' ').count();
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

/// Split into words, each keeping the spaces that follow it. A hard break
/// (a `\n` Sc) is its own word.
fn split_words(chars: &[Sc]) -> Vec<&[Sc]> {
    let mut words = Vec::new();
    let mut start = 0;
    for index in 1..chars.len() {
        let (prev, cur) = (chars[index - 1].0, chars[index].0);
        if cur == '\n' || prev == '\n' || (cur != ' ' && prev == ' ') {
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
        let text = chars.iter().map(|sc| sc.0).collect::<String>();
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

/// Textual collapses whitespace runs per text token, never across tokens.
pub(super) fn collapse_ws(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        if !c.is_whitespace() {
            out.push(c);
        } else if !out.ends_with(' ') {
            out.push(' ');
        }
    }
    out
}

pub(crate) fn cell_width(chars: &[Sc]) -> usize {
    chars.iter().map(|sc| sc.0).collect::<String>().width()
}

fn trim_end(mut chars: Vec<Sc>) -> Vec<Sc> {
    while chars.last().is_some_and(|sc| sc.0 == ' ') {
        chars.pop();
    }
    chars
}

/// Merge consecutive same-style chars into spans.
pub(super) fn merge(chars: &[Sc]) -> Vec<Span<'static>> {
    let mut spans = Vec::new();
    let mut buf = String::new();
    let mut style: Option<Style> = None;
    for &(c, s, _) in chars {
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
