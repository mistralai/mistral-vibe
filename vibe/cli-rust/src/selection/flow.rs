//! Flow selection: resolve endpoints into per-row body spans, snapped to words.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;

use crate::selection::cells::{is_blank, skip_blanks_right, word_end, word_start};
use crate::selection::region::RowSpan;
use crate::selection::Granularity;

/// Leading glyphs Textual paints as non-selectable chrome; each takes its own
/// column plus the space after it before the selectable body starts. That
/// trailing space is part of the match: body text may legitimately open with one
/// of these glyphs (a wrapped row starting with an absolute path), and only the
/// chrome ones are followed by their own padding column.
const CHROME_GLYPHS: [&str; 8] = [">", "/", "⎢", "⎣", "⏵", "⏷", "■", "▌"];
const CHROME_WIDTH: u16 = 2;
/// Assistant markdown is inset by two display-only cells. Selection starts
/// after that inset but keeps any additional, content-owned indentation.
const BODY_PADDING: u16 = 2;
/// Horizontal rules are decoration, never selectable text.
const RULE: &str = "─";

/// A row under the selection: its selectable body `[lo, hi]` and clipped `[x0, x1]`.
struct Row {
    y: u16,
    lo: u16,
    hi: u16,
    x0: u16,
    x1: u16,
}

/// The selection endpoints and how the region maps them onto rows.
pub struct Flow {
    pub selection: ((u16, i32), (u16, i32)),
    pub origin: i32,
    pub end_exclusive: bool,
    /// Whether the region has transcript chrome.
    pub chrome: bool,
}

pub fn resolve(
    span: Flow,
    granularity: Granularity,
    buf: &Buffer,
    chat: Rect,
    gutters: &[(u16, u16, u16)],
    copy_blank_rows: bool,
) -> Vec<RowSpan> {
    let (anchor, head) = span.selection;
    let span_chrome = span.chrome;
    let flow = flow(anchor, head, chat, span.origin, span.end_exclusive);
    let rows: Vec<Row> = flow
        .into_iter()
        .filter_map(|span| {
            body(
                buf,
                chat,
                span,
                gutter_at(gutters, span.0),
                copy_blank_rows,
                span_chrome,
            )
        })
        .collect();
    snap(buf, rows, granularity)
}

/// Inclusive per-row flow-selection spans `(y, x0, x1)`, clamped to `chat`.
fn flow(
    anchor: (u16, i32),
    head: (u16, i32),
    chat: Rect,
    origin: i32,
    end_exclusive: bool,
) -> Vec<RowSpan> {
    let (start, end) = if (anchor.1, anchor.0) <= (head.1, head.0) {
        (anchor, head)
    } else {
        (head, anchor)
    };
    let (left, right) = (chat.x, chat.x + chat.width.saturating_sub(1));
    // Stock Textual selects up to the offset under the pointer, not through it,
    // unless the pointer sits on the last column, where the offset clamps past it.
    let last = u16::from(end_exclusive && end.0 > left && end.0 < right);
    let top = chat.y as i32 - origin;
    let bottom = chat.y as i32 + chat.height as i32 - 1 - origin;
    if start.1 > bottom || end.1 < top {
        return Vec::new();
    }
    (start.1.max(top)..=end.1.min(bottom))
        .map(|y| {
            let x0 = if y == start.1 { start.0 } else { left };
            let x1 = if y == end.1 { end.0 - last } else { right };
            (
                (y + origin) as u16,
                x0.max(left).min(right),
                x1.max(left).min(right),
            )
        })
        .collect()
}

/// The diff gutter width covering screen row `y`, or 0 outside an edit-diff view.
fn gutter_at(gutters: &[(u16, u16, u16)], y: u16) -> u16 {
    gutters
        .iter()
        .find(|(top, bottom, _)| y >= *top && y < *bottom)
        .map_or(0, |(_, _, gutter)| *gutter)
}

/// Clip one flow span to the row's selectable body, dropping pure-chrome rows.
fn body(
    buf: &Buffer,
    chat: Rect,
    span: RowSpan,
    gutter: u16,
    copy_blank_rows: bool,
    chrome: bool,
) -> Option<Row> {
    let (y, mut x0, mut x1) = span;
    let row_end = chat.x + chat.width.saturating_sub(1);
    let padding = if chrome { BODY_PADDING } else { 0 };
    let content_start = chat.x.saturating_add(padding).min(row_end);
    let visible: Vec<(u16, &str)> = (chat.x..=row_end)
        .filter_map(|x| {
            let symbol = buf.cell((x, y))?.symbol();
            (!symbol.trim().is_empty()).then_some((x, symbol))
        })
        .collect();
    let Some((&(first_x, first_symbol), &(last_x, _))) = visible.first().zip(visible.last()) else {
        if !copy_blank_rows {
            return None;
        }
        x0 = x0.max(content_start);
        return (x0 <= x1).then_some(Row {
            y,
            lo: content_start,
            hi: content_start,
            x0,
            x1: x0,
        });
    };
    if chrome && visible.iter().all(|(_, symbol)| *symbol == RULE) {
        return None;
    }
    let mut lo = first_x.min(content_start);
    if chrome && CHROME_GLYPHS.contains(&first_symbol) && is_blank(buf, first_x + 1, y) {
        // Expanded tool groups nest their own border around an effect's border
        // (`  ⎢   ⎢ `). Textual excludes every leading chrome run before
        // applying the diff's line-number gutter, so do the same here.
        let chrome_end = visible
            .iter()
            .take_while(|(x, symbol)| {
                CHROME_GLYPHS.contains(symbol) && is_blank(buf, x.saturating_add(1), y)
            })
            .map(|(x, _)| x.saturating_add(CHROME_WIDTH))
            .last()
            .unwrap_or(first_x.saturating_add(CHROME_WIDTH));
        lo = chrome_end.saturating_add(gutter);
    }
    x0 = x0.max(lo);
    if x0 > x1 {
        return None;
    }
    while is_blank(buf, x1, y) {
        if x1 == x0 {
            return None;
        }
        x1 -= 1;
    }
    (x0 <= x1).then_some(Row {
        y,
        lo: lo.min(last_x),
        hi: last_x,
        x0,
        x1,
    })
}

/// Widen the outer ends to whole words or whole rows (Python `_snap_endpoint`).
fn snap(buf: &Buffer, mut rows: Vec<Row>, granularity: Granularity) -> Vec<RowSpan> {
    let last = match (granularity, rows.len()) {
        (Granularity::Char, _) | (_, 0) => return finish(rows),
        (_, len) => len - 1,
    };
    if granularity == Granularity::Paragraph {
        let first = &mut rows[0];
        first.x0 = skip_blanks_right(buf, first.lo, first.hi, first.y);
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
