//! Bounded collection of one visible approval-detail window.

use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

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

pub(super) fn push_styled_wrapped(
    builder: &mut Builder,
    spans: Vec<Span<'static>>,
    width: u16,
    background: Option<Color>,
) {
    let width = usize::from(width.max(1));
    let mut row = Vec::new();
    let mut used = 0usize;
    for span in spans {
        let style = background.map_or(span.style, |color| span.style.bg(color));
        let mut chunk = String::new();
        for character in span.content.chars() {
            let cells = character.width().unwrap_or(0);
            if used > 0 && used.saturating_add(cells) > width {
                push_chunk(&mut row, &mut chunk, style);
                push_styled_row(builder, &mut row, used, width, background);
                if builder.done() {
                    return;
                }
                used = 0;
            }
            chunk.push(character);
            used = used.saturating_add(cells);
        }
        push_chunk(&mut row, &mut chunk, style);
    }
    push_styled_row(builder, &mut row, used, width, background);
}

fn push_chunk(row: &mut Vec<Span<'static>>, chunk: &mut String, style: Style) {
    if !chunk.is_empty() {
        row.push(Span::styled(std::mem::take(chunk), style));
    }
}

fn push_styled_row(
    builder: &mut Builder,
    row: &mut Vec<Span<'static>>,
    used: usize,
    width: usize,
    background: Option<Color>,
) {
    if let Some(color) = background {
        row.push(Span::styled(
            " ".repeat(width.saturating_sub(used)),
            Style::default().bg(color),
        ));
    }
    builder.push(Line::from(std::mem::take(row)));
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
            if !row.is_empty() {
                if !emit(&row) {
                    return false;
                }
                row.clear();
                row_width = 0;
            }
            let (head, tail, head_width) = split_at_width(word, width);
            if !emit(head) {
                return false;
            }
            emitted = true;
            word = tail;
            word_width = word_width.saturating_sub(head_width);
        }
        let separator = usize::from(index > 0 && (!row.is_empty() || !emitted));
        if !row.is_empty() && row_width + separator + word_width > width {
            if !emit(&row) {
                return false;
            }
            emitted = true;
            row.clear();
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

fn split_at_width(word: &str, width: usize) -> (&str, &str, usize) {
    let mut used = 0;
    for (index, character) in word.char_indices() {
        let cell = character.width().unwrap_or(0);
        if index > 0 && used + cell > width {
            let (head, tail) = word.split_at(index);
            return (head, tail, used);
        }
        used += cell;
    }
    (word, "", used)
}
