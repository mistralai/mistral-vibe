//! Logical markdown table-cell selection independent of painted borders.

use std::ops::Range;
use std::sync::Arc;

use ratatui::layout::Rect;

use super::region::RowSpan;
use super::Granularity;
use crate::app::App;
use crate::utils::input_edit::is_word;

#[derive(Clone, PartialEq, Eq)]
pub struct TableCellKey {
    pub entry_id: String,
    pub table: usize,
    pub row: usize,
    pub column: usize,
}

#[derive(Clone)]
pub struct TableCellSpan {
    pub y: u16,
    pub x0: u16,
    pub x1: u16,
    pub text_start: usize,
    pub text_end: usize,
}

#[derive(Clone)]
pub struct TableCellSelection {
    pub key: TableCellKey,
    text: Arc<str>,
    anchor: usize,
    head: usize,
}

pub struct TableCellHit {
    pub key: TableCellKey,
    pub area: Rect,
    pub spans: Vec<TableCellSpan>,
    pub text: Arc<str>,
}

pub(super) fn selection_at(app: &App, at: (u16, u16)) -> Option<TableCellSelection> {
    let point = document_point(app, at)?;
    app.view
        .table_hitmap
        .iter()
        .find(|cell| cell.contains(point))
        .and_then(|cell| cell.selection_at(point))
}

pub(super) fn drag(app: &mut App, at: (u16, u16)) -> Option<i32> {
    let point = document_point(app, at)?;
    drag_to_document_point(app, point)
}

pub(super) fn follow_scroll(app: &mut App) {
    let Some((x, y)) = app
        .selection
        .region
        .as_ref()
        .map(|selection| selection.head)
    else {
        return;
    };
    let Ok(y) = u16::try_from(y) else {
        return;
    };
    drag_to_document_point(app, (x, y));
}

pub(super) fn head_screen_row(app: &App) -> Option<i32> {
    let selected = app.selection.region.as_ref()?.table_cell.as_ref()?;
    let cell = app
        .view
        .table_hitmap
        .iter()
        .find(|cell| cell.key == selected.key)?;
    selected
        .head_row(cell)
        .map(|row| i32::from(row) + app.view.selection_region.top)
}

fn drag_to_document_point(app: &mut App, point: (u16, u16)) -> Option<i32> {
    let selected = app
        .selection
        .region
        .as_mut()
        .and_then(|selection| selection.table_cell.as_mut())?;
    let cell = app
        .view
        .table_hitmap
        .iter()
        .find(|cell| cell.key == selected.key)?;
    selected.drag_to(cell, point);
    selected
        .head_row(cell)
        .map(|row| i32::from(row) + app.view.selection_region.top)
}

pub(super) fn screen_spans(app: &App, selected: &TableCellSelection, chat: Rect) -> Vec<RowSpan> {
    let Some(cell) = app
        .view
        .table_hitmap
        .iter()
        .find(|cell| cell.key == selected.key)
    else {
        return Vec::new();
    };
    selected
        .spans(cell, app.selection.granularity)
        .into_iter()
        .filter_map(|(document_y, x0, x1)| {
            let y = i32::from(document_y) + app.view.selection_region.top;
            (y >= i32::from(chat.y) && y < i32::from(chat.bottom())).then_some((
                y as u16,
                x0.max(chat.x),
                x1.min(chat.right().saturating_sub(1)),
            ))
        })
        .filter(|(_, x0, x1)| x0 <= x1)
        .collect()
}

fn document_point(app: &App, at: (u16, u16)) -> Option<(u16, u16)> {
    let y = i32::from(at.1) - app.view.selection_region.top;
    Some((at.0, u16::try_from(y).ok()?))
}

impl TableCellHit {
    pub(super) fn contains(&self, at: (u16, u16)) -> bool {
        at.0 >= self.area.x
            && at.0 < self.area.right()
            && at.1 >= self.area.y
            && at.1 < self.area.bottom()
    }

    pub(super) fn selection_at(&self, at: (u16, u16)) -> Option<TableCellSelection> {
        let offset = self.offset_at(at)?;
        Some(TableCellSelection {
            key: self.key.clone(),
            text: self.text.clone(),
            anchor: offset,
            head: offset,
        })
    }

    fn offset_at(&self, at: (u16, u16)) -> Option<usize> {
        let first = self.spans.first()?;
        let last = self.spans.last()?;
        if at.1 < first.y {
            return Some(first.text_start);
        }
        if at.1 > last.y {
            return Some(last.text_end.saturating_sub(1));
        }
        let line = self.spans.iter().find(|span| span.y == at.1);
        let Some(line) = line else {
            return self
                .spans
                .iter()
                .rev()
                .find(|span| span.y < at.1)
                .map(|span| span.text_end.saturating_sub(1));
        };
        if at.0 <= line.x0 {
            return Some(line.text_start);
        }
        if at.0 >= line.x1 {
            return Some(line.text_end.saturating_sub(1));
        }
        Some(line.text_start + usize::from(at.0 - line.x0))
    }
}

impl TableCellSelection {
    pub(crate) fn head_offset(&self) -> usize {
        self.head
    }

    pub(super) fn drag_to(&mut self, cell: &TableCellHit, at: (u16, u16)) {
        if let Some(offset) = cell.offset_at(at) {
            self.head = offset;
        }
    }

    pub(super) fn spans(&self, cell: &TableCellHit, granularity: Granularity) -> Vec<RowSpan> {
        let range = self.range(granularity);
        cell.spans
            .iter()
            .filter_map(|span| {
                let start = range.start.max(span.text_start);
                let end = range.end.min(span.text_end);
                if start >= end {
                    return None;
                }
                let x0 = span
                    .x0
                    .checked_add(u16::try_from(start - span.text_start).ok()?)?;
                let x1 = x0.checked_add(u16::try_from(end - start - 1).ok()?)?;
                (x1 <= span.x1).then_some((span.y, x0, x1))
            })
            .collect()
    }

    pub(super) fn selected_text(&self, granularity: Granularity) -> String {
        let range = self.range(granularity);
        self.text
            .chars()
            .skip(range.start)
            .take(range.len())
            .collect()
    }

    fn head_row(&self, cell: &TableCellHit) -> Option<u16> {
        cell.spans
            .iter()
            .find(|span| self.head >= span.text_start && self.head < span.text_end)
            .map(|span| span.y)
    }

    fn range(&self, granularity: Granularity) -> Range<usize> {
        let chars: Vec<char> = self.text.chars().collect();
        if chars.is_empty() {
            return 0..0;
        }
        if granularity == Granularity::Paragraph {
            return 0..chars.len();
        }
        let mut start = self.anchor.min(self.head).min(chars.len() - 1);
        let mut end = self.anchor.max(self.head).min(chars.len() - 1) + 1;
        if granularity == Granularity::Word {
            while start > 0 && is_word(chars[start - 1]) {
                start -= 1;
            }
            if is_word(chars[end - 1]) {
                while end < chars.len() && is_word(chars[end]) {
                    end += 1;
                }
            }
        }
        start..end
    }
}
