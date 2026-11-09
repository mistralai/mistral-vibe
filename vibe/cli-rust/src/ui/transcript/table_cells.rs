//! Translate entry-local markdown table cells into transcript document coordinates.

use ratatui::layout::Rect;

use crate::selection::{TableCellHit, TableCellKey, TableCellSpan};
use crate::transcript::TranscriptEntry;
use crate::ui::markdown;

pub(super) fn collect(
    entry: &TranscriptEntry<'_>,
    cells: &[markdown::TableCell],
    document_y: u16,
    document_x: u16,
    visible_top: u16,
    visible_bottom: u16,
    selected: Option<&(TableCellKey, usize)>,
) -> Vec<TableCellHit> {
    cells
        .iter()
        .filter_map(|cell| {
            let area = Rect {
                x: cell.area.x.saturating_add(document_x),
                y: cell.area.y.saturating_add(document_y),
                ..cell.area
            };
            let visible = area.y < visible_bottom && area.bottom() > visible_top;
            let selected_head = selected.and_then(|(key, head)| {
                (key.entry_id == entry.id
                    && key.table == cell.table
                    && key.row == cell.row
                    && key.column == cell.column)
                    .then_some(*head)
            });
            if !visible && selected_head.is_none() {
                return None;
            }
            Some(TableCellHit {
                key: TableCellKey {
                    entry_id: entry.id.to_owned(),
                    table: cell.table,
                    row: cell.row,
                    column: cell.column,
                },
                area,
                spans: visible_spans(
                    &cell.spans,
                    document_y,
                    document_x,
                    visible_top,
                    visible_bottom,
                    selected_head,
                ),
                text: cell.text.clone(),
            })
        })
        .collect()
}

fn visible_spans(
    spans: &[markdown::TableCellSpan],
    document_y: u16,
    document_x: u16,
    visible_top: u16,
    visible_bottom: u16,
    selected_head: Option<usize>,
) -> Vec<TableCellSpan> {
    let before = spans
        .iter()
        .rposition(|span| span.y.saturating_add(document_y) < visible_top);
    let after = spans
        .iter()
        .position(|span| span.y.saturating_add(document_y) >= visible_bottom);
    spans
        .iter()
        .enumerate()
        .filter_map(|(index, span)| {
            let y = span.y.saturating_add(document_y);
            let visible = y >= visible_top && y < visible_bottom;
            let owns_head =
                selected_head.is_some_and(|head| head >= span.text_start && head < span.text_end);
            (visible || owns_head || before == Some(index) || after == Some(index)).then_some(
                TableCellSpan {
                    y,
                    x0: span.x0.saturating_add(document_x),
                    x1: span.x1.saturating_add(document_x),
                    text_start: span.text_start,
                    text_end: span.text_end,
                },
            )
        })
        .collect()
}
