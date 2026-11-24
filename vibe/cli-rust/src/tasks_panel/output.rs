//! A background task's output: its latest bytes, cleaned into lines numbered from the task's first one, and wrapped.

use std::ops::Range;

use crate::utils::clean::clean_output;
use crate::utils::text::{expand_tabs, wrap_ranges};

#[cfg(test)]
mod tests;

/// Bytes the view keeps; older output scrolls away.
pub(super) const KEEP_BYTES: usize = 128_000;

/// A wrapped row: a byte range of a line, numbered from the task's first line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub line: usize,
    pub range: Range<usize>,
}

/// Every kept line's rows at `width`.
struct Wrap {
    width: u16,
    rows: Vec<Row>,
}

/// What a page changed: the rows the byte cap dropped above, whether it cut the first kept line, and the first line it wrote.
#[derive(Debug, Default)]
pub struct Appended {
    pub rows: usize,
    pub mid_line: bool,
    pub from: usize,
}

/// The output of the task the tasks window shows.
pub struct Output {
    pub process_id: String,
    pub command: String,
    pub(super) generation: u64,
    pub(super) cursor: u64,
    pub unavailable: bool,
    pub loaded: bool,
    bytes: Vec<u8>,
    /// Where the line still being written starts in `bytes`.
    tail: usize,
    /// Every line of `bytes`, the one still being written last.
    lines: Vec<String>,
    /// The number of `lines[0]`, counted from the task's first line.
    first_line: usize,
    wrap: Option<Wrap>,
}

impl Output {
    pub(super) fn new(process_id: String, command: String, generation: u64) -> Self {
        Self {
            process_id,
            command,
            generation,
            cursor: 0,
            unavailable: false,
            loaded: false,
            bytes: Vec::new(),
            tail: 0,
            lines: Vec::new(),
            first_line: 0,
            wrap: None,
        }
    }

    pub fn first_line(&self) -> usize {
        self.first_line
    }

    /// The lines shown, trailing blank ones left out.
    pub fn lines(&self) -> &[String] {
        let blank = self.lines.iter().rev();
        let blank = blank.take_while(|line| line.trim().is_empty()).count();
        &self.lines[..self.lines.len() - blank]
    }

    /// The width the last frame wrapped the output to.
    pub fn width(&self) -> Option<u16> {
        self.wrap.as_ref().map(|wrap| wrap.width)
    }

    /// Wrap to `width` unless already; true when that replaced another width's rows.
    pub fn wrap(&mut self, width: u16) -> bool {
        let before = self.width();
        if before == Some(width) {
            return false;
        }
        let mut rows = Vec::new();
        for (index, line) in self.lines.iter().enumerate() {
            rows.extend(rows_of(self.first_line + index, line, width));
        }
        self.wrap = Some(Wrap { width, rows });
        before.is_some()
    }

    /// The shown lines' rows at the last frame's width.
    pub fn rows(&self) -> &[Row] {
        let Some(wrap) = &self.wrap else {
            return &[];
        };
        let end = self.first_line + self.lines().len();
        &wrap.rows[..wrap.rows.partition_point(|row| row.line < end)]
    }

    pub fn text(&self, row: &Row) -> &str {
        let line = &self.lines[row.line - self.first_line];
        &line[row.range.clone()]
    }

    /// The row showing byte `at` of `line`.
    pub fn row_of(&self, (line, at): (usize, usize)) -> Option<usize> {
        let rows = self.rows();
        let after = rows.partition_point(|row| (row.line, row.range.start) <= (line, at));
        after.checked_sub(1)
    }

    /// Rows the shown lines wrap to at `width`, up to `limit`, so the window never wraps them all every frame.
    pub fn tail_rows(&self, width: u16, limit: usize) -> usize {
        if self.width() == Some(width) {
            return self.rows().len().min(limit);
        }
        let mut total = 0;
        for line in self.lines().iter().rev() {
            if total >= limit {
                break;
            }
            total += wrap_ranges(line, usize::from(width)).len();
        }
        total.min(limit)
    }

    /// Add a page: the line still being written and the new ones are cleaned and wrapped, the oldest dropped past the byte cap.
    pub(super) fn append(&mut self, bytes: &[u8]) -> Appended {
        self.bytes.extend_from_slice(bytes);
        // A progress line redrawn with `\r` keeps only its last write, so redraws never push older lines past the cap.
        if self.bytes[self.tail..].contains(&b'\r') {
            let kept = last_writes(&self.bytes[self.tail..]);
            self.bytes.truncate(self.tail);
            self.bytes.extend_from_slice(&kept);
        }
        let mid_line = self.trim();
        let kept = self.lines.len().saturating_sub(1);
        self.lines.truncate(kept);
        let text = String::from_utf8_lossy(&self.bytes[self.tail..]);
        let cleaned = clean_output(&text);
        self.lines.extend(cleaned.split('\n').map(expand_tabs));
        if let Some(at) = self.bytes[self.tail..].iter().rposition(|b| *b == b'\n') {
            self.tail += at + 1;
        }
        let from = self.first_line + kept;
        let mut appended = Appended {
            rows: 0,
            mid_line,
            from,
        };
        let Some(wrap) = self.wrap.as_mut() else {
            return appended;
        };
        appended.rows = wrap.rows.partition_point(|row| row.line < self.first_line);
        wrap.rows.drain(..appended.rows);
        wrap.rows
            .truncate(wrap.rows.partition_point(|row| row.line < from));
        for (index, line) in self.lines.iter().enumerate().skip(kept) {
            wrap.rows
                .extend(rows_of(self.first_line + index, line, wrap.width));
        }
        appended
    }

    /// Drop everything shown, for a read that jumps to the latest output; nothing anchored to the old lines holds.
    pub(super) fn restart(&mut self) -> Appended {
        let shown = !self.lines.is_empty();
        let rows = self
            .wrap
            .as_mut()
            .map_or(0, |wrap| wrap.rows.drain(..).count());
        self.first_line += self.lines.len();
        (self.bytes, self.tail, self.lines) = (Vec::new(), 0, Vec::new());
        Appended {
            rows,
            mid_line: shown,
            from: self.first_line,
        }
    }

    /// Drop bytes past the cap from a line's start; true when the first kept line lost its start, which then takes a new number.
    fn trim(&mut self) -> bool {
        let Some(cut) = self
            .bytes
            .len()
            .checked_sub(KEEP_BYTES)
            .filter(|cut| *cut > 0)
        else {
            return false;
        };
        let (cut, mid_line) = match self.bytes[cut - 1] == b'\n' {
            true => (cut, false),
            false => {
                let (start, whole) = line_start(&self.bytes[cut..]);
                (cut + start, !whole)
            }
        };
        let gone = self.bytes[..cut].iter().filter(|b| **b == b'\n').count();
        self.bytes.drain(..cut);
        // Lines the page brought are cleaned whole; only lines cleaned before keep their text.
        match cut <= self.tail {
            true => drop(self.lines.drain(..gone)),
            false => self.lines.clear(),
        }
        self.tail = self.tail.saturating_sub(cut);
        self.first_line += gone + usize::from(mid_line);
        mid_line
    }
}

/// Where the first whole line of `bytes`, which starts mid-output, begins, and whether one does; without a later line to show, the cut one keeps its tail from a character's start.
pub(super) fn line_start(bytes: &[u8]) -> (usize, bool) {
    let next = bytes.iter().position(|b| *b == b'\n').map(|at| at + 1);
    match next.filter(|at| bytes[*at..].iter().any(|b| !b"\r\n".contains(b))) {
        Some(at) => (at, true),
        None => (
            bytes.iter().take_while(|b| **b & 0xC0 == 0x80).count(),
            false,
        ),
    }
}

/// Each line of `bytes` from after its last `\r` that more text follows, as `clean_output` shows it.
fn last_writes(bytes: &[u8]) -> Vec<u8> {
    let mut kept = Vec::with_capacity(bytes.len());
    for (index, line) in bytes.split(|b| *b == b'\n').enumerate() {
        if index > 0 {
            kept.push(b'\n');
        }
        let written = line.len() - line.iter().rev().take_while(|b| **b == b'\r').count();
        let start = line[..written].iter().rposition(|b| *b == b'\r');
        kept.extend_from_slice(&line[start.map_or(0, |at| at + 1)..]);
    }
    kept
}

/// `text`'s rows at `width`, as line `line`.
fn rows_of(line: usize, text: &str, width: u16) -> impl Iterator<Item = Row> {
    let ranges = wrap_ranges(text, usize::from(width));
    ranges.into_iter().map(move |range| Row { line, range })
}
