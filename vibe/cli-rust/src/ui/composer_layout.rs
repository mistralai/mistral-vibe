//! Composer row and scroll calculations.

use std::collections::VecDeque;
use std::ops::RangeBounds;

use unicode_width::UnicodeWidthStr;

use super::tab_cells::{cells, cells_width, Cell};

/// Visual row as `(start, end, column)`: body bytes and its logical-line column.
type Range = (usize, usize, usize);

#[derive(Clone, Copy)]
pub struct Row<'a> {
    pub index: usize,
    pub start: usize,
    pub text: &'a str,
    pub column: usize,
    final_in_line: bool,
}

impl Row<'_> {
    pub fn end(self) -> usize {
        self.start + self.text.len()
    }

    /// Row bytes in `bytes` as drawn, tabs expanded to spaces.
    pub fn shown(self, bytes: impl RangeBounds<usize>) -> String {
        cells(self.text, self.column)
            .filter(|cell| bytes.contains(&cell.byte))
            .map(Cell::shown)
            .collect()
    }
}

#[derive(Clone, Copy)]
struct Glyph {
    start: usize,
    end: usize,
    width: u16,
    column: usize,
    whitespace: bool,
}

fn wrapped_ranges(text: &str, width: u16) -> Vec<Range> {
    let glyphs = cells(text, 0).map(|cell| Glyph {
        start: cell.byte,
        end: cell.byte + cell.symbol.len(),
        width: cell.width as u16,
        column: cell.column,
        whitespace: cell.symbol.chars().all(char::is_whitespace),
    });
    let mut rows = Vec::new();
    let mut line: Vec<Glyph> = Vec::new();
    let mut word: Vec<Glyph> = Vec::new();
    let mut whitespace: VecDeque<Glyph> = VecDeque::new();
    let (mut line_width, mut word_width, mut whitespace_width) = (0, 0, 0);
    let mut non_whitespace_previous = false;
    for glyph in glyphs {
        if glyph.width > width {
            continue;
        }
        let word_found = non_whitespace_previous && glyph.whitespace;
        let word_overflow = line.is_empty() && word_width + whitespace_width + glyph.width > width;
        if word_found || word_overflow {
            line.extend(whitespace.drain(..));
            line_width += whitespace_width;
            line.append(&mut word);
            line_width += word_width;
            whitespace_width = 0;
            word_width = 0;
        }
        if line_width >= width
            || (glyph.width > 0 && line_width + whitespace_width + word_width + glyph.width > width)
        {
            let mut remaining = width.saturating_sub(line_width);
            push_range(&mut rows, &line, (glyph.start, glyph.column));
            line.clear();
            line_width = 0;
            while let Some(pending) = whitespace.front() {
                if pending.width > remaining {
                    break;
                }
                whitespace_width -= pending.width;
                remaining -= pending.width;
                whitespace.pop_front();
            }
            if glyph.whitespace && whitespace.is_empty() {
                continue;
            }
        }
        if glyph.whitespace {
            whitespace_width += glyph.width;
            whitespace.push_back(glyph);
        } else {
            word_width += glyph.width;
            word.push(glyph);
        }
        non_whitespace_previous = !glyph.whitespace;
    }
    line.extend(whitespace);
    line.append(&mut word);
    if !line.is_empty() {
        push_range(&mut rows, &line, (text.len(), 0));
    }
    if rows.is_empty() {
        rows.push((0, 0, 0));
    }
    rows
}

fn push_range(rows: &mut Vec<Range>, glyphs: &[Glyph], (byte, column): (usize, usize)) {
    rows.push(
        glyphs
            .first()
            .zip(glyphs.last())
            .map_or((byte, byte, column), |(first, last)| {
                (first.start, last.end, first.column)
            }),
    );
}

fn visual_position(rows: &[Range], text: &str, cursor: usize) -> (usize, usize) {
    let row = rows
        .partition_point(|&(start, ..)| start <= cursor)
        .saturating_sub(1)
        .min(rows.len().saturating_sub(1));
    let (start, end, column) = rows[row];
    let cursor = cursor.clamp(start, end);
    (row, cells_width(&text[start..cursor], column))
}

fn visual_rows(body: &str, width: u16) -> Vec<Range> {
    rows_with(body, |text| wrapped_ranges(text, width))
}

fn hard_wrapped_ranges(text: &str, width: u16) -> Vec<Range> {
    let mut rows = Vec::new();
    let (mut start, mut column, mut used) = (0, 0, 0);
    for cell in cells(text, 0) {
        let symbol_width = cell.width as u16;
        if cell.byte > start && used + symbol_width > width {
            rows.push((start, cell.byte, column));
            start = cell.byte;
            column += usize::from(used);
            used = 0;
        }
        used += symbol_width;
    }
    rows.push((start, text.len(), column));
    rows
}

fn rows_with(body: &str, rows: impl Fn(&str) -> Vec<Range>) -> Vec<Range> {
    let mut visual = Vec::new();
    let mut body_start = 0;
    for text in body.split('\n') {
        visual.extend(
            rows(text)
                .into_iter()
                .map(|(start, end, column)| (body_start + start, body_start + end, column)),
        );
        body_start += text.len() + 1;
    }
    visual
}

pub struct ComposerLayout<'a> {
    body: &'a str,
    cursor: usize,
    visual_rows: Vec<Range>,
}

impl<'a> ComposerLayout<'a> {
    pub fn new(body: &'a str, marker: &str, cursor: usize, width: u16) -> Self {
        let content_width = width
            .saturating_sub(1)
            .saturating_sub(marker.width() as u16)
            .max(1);
        Self {
            body,
            cursor,
            visual_rows: visual_rows(body, content_width),
        }
    }

    pub fn hard_wrapped(body: &'a str, cursor: usize, width: u16) -> Self {
        let width = width.max(1);
        let mut visual_rows = rows_with(body, |text| hard_wrapped_ranges(text, width));
        let (_, cursor_column) = visual_position(&visual_rows, body, cursor);
        if width > 1 && cursor_column == usize::from(width) {
            visual_rows = rows_with(body, |text| hard_wrapped_ranges(text, width - 1));
        }
        Self {
            body,
            cursor,
            visual_rows,
        }
    }

    fn row_at(&self, index: usize) -> Option<Row<'a>> {
        let &(start, end, column) = self.visual_rows.get(index)?;
        Some(Row {
            index,
            start,
            text: &self.body[start..end],
            column,
            final_in_line: end == self.body.len() || self.body.as_bytes().get(end) == Some(&b'\n'),
        })
    }

    pub fn rows(&self) -> impl DoubleEndedIterator<Item = Row<'a>> + '_ {
        (0..self.visual_rows.len())
            .map(|index| self.row_at(index).expect("row index comes from the layout"))
    }

    pub fn caret_in(&self, row: Row<'_>) -> Option<usize> {
        let (cursor_row, _) = visual_position(&self.visual_rows, self.body, self.cursor);
        (cursor_row == row.index).then(|| self.cursor.clamp(row.start, row.end()) - row.start)
    }

    pub fn height(&self) -> u16 {
        u16::try_from(self.visual_rows.len()).unwrap_or(u16::MAX)
    }

    pub fn scroll(&self, viewport_rows: usize) -> u16 {
        self.caret_row()
            .saturating_sub(viewport_rows.saturating_sub(1)) as u16
    }

    /// Keep `top` as the first visible row, moving it the minimum needed to
    /// reveal the caret, so the view only scrolls once the caret leaves it.
    pub fn viewport_top(&self, top: usize, viewport_rows: usize) -> usize {
        let caret = self.caret_row();
        let last_top = self.visual_rows.len().saturating_sub(viewport_rows.max(1));
        top.min(caret)
            .max(caret.saturating_sub(viewport_rows.saturating_sub(1)))
            .min(last_top)
    }

    pub fn caret_row(&self) -> usize {
        visual_position(&self.visual_rows, self.body, self.cursor).0
    }

    pub fn page_cursor(&self, viewport_rows: usize, down: bool) -> usize {
        let (current_row, column) = visual_position(&self.visual_rows, self.body, self.cursor);
        let target_row = if down {
            current_row
                .saturating_add(viewport_rows)
                .min(self.visual_rows.len().saturating_sub(1))
        } else {
            current_row.saturating_sub(viewport_rows)
        };
        self.offset_at(target_row, column)
    }

    pub fn vertical_offset(&self, down: bool) -> (usize, bool) {
        let (current, column) = visual_position(&self.visual_rows, self.body, self.cursor);
        let target = if down {
            (current + 1 < self.visual_rows.len()).then_some(current + 1)
        } else {
            current.checked_sub(1)
        };
        let Some(target) = target else {
            return (if down { self.body.len() } else { 0 }, false);
        };
        (
            self.row_at(target)
                .map_or(self.cursor, |row| offset_in_row(row, column)),
            true,
        )
    }

    pub fn offset_at(&self, visual_row: usize, column: usize) -> usize {
        self.row_at(visual_row)
            .or_else(|| self.row_at(self.visual_rows.len().saturating_sub(1)))
            .map_or(0, |row| offset_in_row(row, column))
    }
}

fn offset_in_row(row: Row<'_>, column: usize) -> usize {
    let mut offset = row.text.len();
    let mut used = 0;
    for cell in cells(row.text, row.column) {
        if column < used + cell.width {
            offset = cell.byte;
            break;
        }
        used += cell.width;
    }
    if offset == row.text.len() && !row.final_in_line {
        offset = cells(row.text, row.column)
            .last()
            .map_or(0, |cell| cell.byte);
    }
    row.start + offset
}
