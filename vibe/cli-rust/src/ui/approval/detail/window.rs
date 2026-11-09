//! Bounded collection of one visible approval-detail window.

use ratatui::style::Style;
use ratatui::text::{Line, Span};
use unicode_width::UnicodeWidthStr;

use crate::ui::styled_text;
use crate::utils::text::split_at_width;

pub(in crate::ui::approval) struct Rows {
    pub(in crate::ui::approval) lines: Vec<Line<'static>>,
    pub(in crate::ui::approval) total: usize,
}

pub(super) struct Builder {
    start: usize,
    end: usize,
    seen: usize,
    known_total: Option<usize>,
    lines: Vec<Line<'static>>,
}

impl Builder {
    pub fn new(start: usize, count: usize, known_total: Option<usize>) -> Self {
        Self {
            start,
            end: start.saturating_add(count),
            seen: 0,
            known_total,
            lines: Vec::with_capacity(count),
        }
    }

    pub fn push(&mut self, line: Line<'static>) {
        if self.seen >= self.start && self.seen < self.end {
            self.lines.push(line);
        }
        self.seen = self.seen.saturating_add(1);
    }

    pub fn push_with(&mut self, line: impl FnOnce() -> Line<'static>) {
        if self.seen >= self.start && self.seen < self.end {
            self.lines.push(line());
        }
        self.seen = self.seen.saturating_add(1);
    }

    pub fn push_text(&mut self, text: &str, style: Style) {
        if self.seen >= self.start && self.seen < self.end {
            self.lines
                .push(Line::from(Span::styled(text.to_owned(), style)));
        }
        self.seen = self.seen.saturating_add(1);
    }

    pub fn done(&self) -> bool {
        self.known_total.is_some() && self.seen >= self.end
    }

    pub fn finish(self) -> Rows {
        Rows {
            lines: self.lines,
            total: self.known_total.unwrap_or(self.seen),
        }
    }
}

pub(super) fn push_wrapped(builder: &mut Builder, text: &str, width: u16, style: Style) {
    for_each_wrapped(text, usize::from(width.max(1)), |row| {
        builder.push_text(row, style);
        !builder.done()
    });
}

pub(super) fn push_prefixed_wrapped(
    builder: &mut Builder,
    text: &str,
    prefix: &str,
    width: u16,
    style: Style,
) {
    let content_width = usize::from(width).saturating_sub(prefix.width()).max(1);
    for_each_wrapped(text, content_width, |row| {
        builder.push_text(&format!("{prefix}{row}"), style);
        !builder.done()
    });
}

/// Word-wrap one bounded styled line, like assistant prose.
pub(super) fn push_styled_wrapped(builder: &mut Builder, spans: &[Span<'static>], width: u16) {
    for row in styled_text::wrap_hard(spans, usize::from(width.max(1))) {
        builder.push(Line::from(row));
        if builder.done() {
            return;
        }
    }
}

fn for_each_wrapped(text: &str, width: usize, mut emit: impl FnMut(&str) -> bool) {
    for line in text.split('\n') {
        if !wrap_line(line, width, &mut emit) {
            return;
        }
    }
}

fn wrap_line(line: &str, width: usize, emit: &mut impl FnMut(&str) -> bool) -> bool {
    let mut row = String::new();
    let mut row_width = 0usize;
    let mut emitted = false;
    for (index, word) in line.split(' ').enumerate() {
        let mut word = word;
        let mut word_width = word.width();
        while word_width > width {
            let (head, tail, head_width) = split_at_width(word, width);
            if tail.is_empty() {
                word_width = head_width;
                break;
            }
            if !row.is_empty() {
                if !flush(&mut row, emit) {
                    return false;
                }
                row_width = 0;
            }
            if !emit(head) {
                return false;
            }
            emitted = true;
            word = tail;
        }
        let separator = usize::from(index > 0 && (!row.is_empty() || !emitted));
        if !row.is_empty() && row_width + separator + word_width > width {
            if !flush(&mut row, emit) {
                return false;
            }
            emitted = true;
            row_width = 0;
        } else if separator == 1 {
            row.push(' ');
            row_width += 1;
        }
        row.push_str(word);
        row_width = row_width.saturating_add(word_width);
    }
    emit(&row)
}

/// Emit a folded `row` unless it is only an indent, then clear it.
fn flush(row: &mut String, emit: &mut impl FnMut(&str) -> bool) -> bool {
    let kept = row.trim().is_empty() || emit(row);
    row.clear();
    kept
}

#[cfg(test)]
mod tests;
