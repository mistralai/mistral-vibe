//! Composer row and scroll calculations.

use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

#[derive(Clone, Copy)]
pub struct Row<'a> {
    pub index: usize,
    pub start: usize,
    pub text: &'a str,
    final_in_line: bool,
}

impl Row<'_> {
    pub fn end(self) -> usize {
        self.start + self.text.len()
    }
}

pub struct ComposerLayout<'a> {
    body: &'a str,
    cursor: usize,
    width: usize,
}

impl<'a> ComposerLayout<'a> {
    pub fn new(body: &'a str, marker: &str, cursor: usize, width: u16) -> Self {
        let inner = width.saturating_sub(1) as usize;
        Self {
            body,
            cursor,
            width: inner.saturating_sub(marker.width()).max(1),
        }
    }

    pub fn rows(&self) -> Vec<Row<'a>> {
        let mut rows = Vec::new();
        let mut line_start = 0;
        for line in self.body.split('\n') {
            let mut section_start = 0;
            for section_end in wrap_offsets(line, self.width)
                .into_iter()
                .chain(std::iter::once(line.len()))
            {
                rows.push(Row {
                    index: rows.len(),
                    start: line_start + section_start,
                    text: &line[section_start..section_end],
                    final_in_line: section_end == line.len(),
                });
                section_start = section_end;
            }
            line_start += line.len() + 1;
        }
        rows
    }

    pub fn caret_in(&self, row: Row<'_>) -> Option<usize> {
        (self.cursor >= row.start
            && (self.cursor < row.end() || row.final_in_line && self.cursor == row.end()))
        .then(|| self.cursor - row.start)
    }

    pub fn height(&self) -> u16 {
        u16::try_from(self.rows().len()).unwrap_or(u16::MAX)
    }

    pub fn scroll(&self, viewport_rows: usize) -> u16 {
        self.caret_row()
            .saturating_sub(viewport_rows.saturating_sub(1)) as u16
    }

    pub fn caret_row(&self) -> usize {
        self.rows()
            .into_iter()
            .position(|row| self.caret_in(row).is_some())
            .unwrap_or(0)
    }

    pub fn vertical_offset(&self, down: bool) -> (usize, bool) {
        let rows = self.rows();
        let current = rows
            .iter()
            .position(|row| self.caret_in(*row).is_some())
            .unwrap_or(0);
        let target = if down {
            (current + 1 < rows.len()).then_some(current + 1)
        } else {
            current.checked_sub(1)
        };
        let Some(target) = target else {
            return (if down { self.body.len() } else { 0 }, false);
        };
        let caret = self.caret_in(rows[current]).unwrap_or(0);
        let column = rows[current].text[..caret].width();
        (offset_in_row(rows[target], column), true)
    }

    pub fn offset_at(&self, visual_row: usize, column: usize) -> usize {
        let rows = self.rows();
        rows.get(visual_row)
            .or_else(|| rows.last())
            .copied()
            .map_or(0, |row| offset_in_row(row, column))
    }
}

fn offset_in_row(row: Row<'_>, column: usize) -> usize {
    let mut offset = row.text.len();
    let mut cells = 0;
    for (byte, ch) in row.text.char_indices() {
        let width = ch.width().unwrap_or(0);
        if column < cells + width {
            offset = byte;
            break;
        }
        cells += width;
    }
    if offset == row.text.len() && !row.final_in_line {
        offset = row
            .text
            .char_indices()
            .next_back()
            .map_or(0, |(byte, _)| byte);
    }
    row.start + offset
}

fn wrap_offsets(text: &str, width: usize) -> Vec<usize> {
    let mut offsets = Vec::new();
    let mut line_width = 0;
    for (start, end) in chunks(text) {
        let chunk = &text[start..end];
        let chunk_width = chunk.width();
        if chunk_width <= width.saturating_sub(line_width) {
            line_width += chunk_width;
            continue;
        }
        if chunk_width <= width {
            push_offset(&mut offsets, start);
            line_width = chunk_width;
            continue;
        }
        push_offset(&mut offsets, start);
        line_width = 0;
        for (relative, ch) in chunk.char_indices() {
            let char_width = ch.width().unwrap_or(0);
            if line_width + char_width > width {
                push_offset(&mut offsets, start + relative);
                line_width = char_width;
            } else {
                line_width += char_width;
            }
        }
    }
    offsets
}

fn chunks(text: &str) -> Vec<(usize, usize)> {
    let mut chunks = Vec::new();
    let mut start = 0;
    while start < text.len() {
        let starts_with_whitespace = text[start..]
            .chars()
            .next()
            .is_some_and(char::is_whitespace);
        let mut end = start;
        let mut reached_whitespace = starts_with_whitespace;
        for (relative, ch) in text[start..].char_indices() {
            if starts_with_whitespace {
                if !ch.is_whitespace() {
                    break;
                }
            } else if reached_whitespace && !ch.is_whitespace() {
                break;
            } else if ch.is_whitespace() {
                reached_whitespace = true;
            }
            end = start + relative + ch.len_utf8();
        }
        chunks.push((start, end));
        start = end;
    }
    chunks
}

fn push_offset(offsets: &mut Vec<usize>, offset: usize) {
    if offset > 0 && offsets.last().copied() != Some(offset) {
        offsets.push(offset);
    }
}
