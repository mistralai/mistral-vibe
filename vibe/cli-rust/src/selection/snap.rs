//! Widen a flow selection's outer ends to whole words or whole rows.

use ratatui::buffer::Buffer;

use crate::selection::cells::{skip_blanks_right, word_end, word_start};
use crate::selection::flow::Row;
use crate::selection::region::RowSpan;
use crate::selection::Granularity;

/// Widen the outer ends to whole words or whole rows; a `/raw` row keeps its indentation.
pub(super) fn snap(
    buf: &Buffer,
    mut rows: Vec<Row>,
    granularity: Granularity,
    raw: impl Fn(u16) -> bool,
) -> Vec<RowSpan> {
    let last = match (granularity, rows.len()) {
        (Granularity::Char, _) | (_, 0) => return finish(rows),
        (_, len) => len - 1,
    };
    if granularity == Granularity::Paragraph {
        let first = &mut rows[0];
        first.x0 = match raw(first.y) {
            true => first.lo,
            false => skip_blanks_right(buf, first.lo, first.hi, first.y),
        };
        rows[last].x1 = rows[last].hi;
        return finish(rows);
    }
    let start = &mut rows[0];
    start.x0 = word_start(buf, start.x0, start.lo, start.y);
    let end = &mut rows[last];
    end.x1 = word_end(buf, end.x1, end.hi, end.y);
    finish(rows)
}

fn finish(rows: Vec<Row>) -> Vec<RowSpan> {
    rows.into_iter()
        .map(|row| (row.y, row.x0, row.x1))
        .collect()
}
