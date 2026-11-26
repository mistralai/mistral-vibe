//! Flow selection: resolve endpoints into per-row body spans, snapped to words.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Color;

use crate::selection::cells::is_blank;
use crate::selection::region::RowSpan;
use crate::selection::row_ranges::{chrome_at, in_raw, RowChrome};
use crate::selection::snap::snap;
use crate::selection::Granularity;
use crate::ui::theme;

/// Leading chrome glyphs, each followed by its own padding column (body text may open with one too).
const CHROME_GLYPHS: [&str; 4] = ["⏵", "⏷", "■", "▌"];
/// Entry border glyphs, chrome only inside the entry's border columns: tool output often draws them too.
const BORDER_GLYPHS: [&str; 2] = ["│", "└"];
/// Prompt and settled markers, chrome only in the first column: deeper in, a wrapped `>` or `/ path` row is content.
const MARKER_GLYPHS: [&str; 3] = [">", "/", "✓"];
const CHROME_WIDTH: u16 = 2;
/// Assistant markdown is inset by two display-only cells. Selection starts
/// after that inset but keeps any additional, content-owned indentation.
const BODY_PADDING: u16 = 2;
/// Horizontal rules and fence box edges are decoration, never selectable text.
const EDGE: [&str; 5] = ["─", "┌", "┐", "└", "┘"];
/// The fence box rail closing a fenced code row after a blank column.
const RAIL: &str = "│";

/// A row under the selection: its selectable body `[lo, hi]` and clipped `[x0, x1]`.
pub(super) struct Row {
    pub y: u16,
    pub lo: u16,
    pub hi: u16,
    pub x0: u16,
    pub x1: u16,
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
    row_chrome: &[RowChrome],
    raw: &[(u16, u16)],
    copy_blank_rows: bool,
) -> Vec<RowSpan> {
    let (anchor, head) = span.selection;
    let span_chrome = span.chrome;
    let end_exclusive = span.end_exclusive && granularity == Granularity::Char;
    let flow = flow(anchor, head, chat, span.origin, end_exclusive);
    let rows: Vec<Row> = flow
        .into_iter()
        .filter_map(|span| {
            let (indent, border, gutter) = chrome_at(row_chrome, span.0);
            // A row a `Worked for` group indents selects as if painted without that indent.
            let indent = indent.min(chat.width);
            let chat = Rect {
                x: chat.x + indent,
                width: chat.width - indent,
                ..chat
            };
            body(
                buf,
                chat,
                span,
                (border, gutter),
                copy_blank_rows,
                span_chrome && !in_raw(raw, span.0),
            )
        })
        .collect();
    snap(buf, rows, granularity, |y| in_raw(raw, y))
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
    // Selection ends before the offset under the pointer, not through it,
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

/// Clip one flow span to the row's selectable body, dropping pure-chrome rows.
fn body(
    buf: &Buffer,
    chat: Rect,
    span: RowSpan,
    (border, gutter): (u16, u16),
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
        return blank_row(y, content_start, x0, x1, copy_blank_rows);
    };
    let boxed = warning_boxed(buf, &visible, y);
    let fence_glyph = |buf: &Buffer, x: u16, y: u16| fence_glyph(buf, x, y, boxed);
    // A fence's blank content rows paint only their two rails: layout, not
    // text. The rails bracket the row, so content — even rails-colored —
    // makes the row wider and keeps it.
    if chrome
        && visible.len() == 2
        && visible
            .iter()
            .all(|&(x, symbol)| symbol == RAIL && fence_glyph(buf, x, y))
    {
        return blank_row(y, content_start + CHROME_WIDTH, x0, x1, copy_blank_rows);
    }
    // Rule rows and fence box edges are decoration, never selectable text:
    // dashes, like any rule paints them, plus the box's cornered edges. A
    // code row quoting either keeps its rails and stays content.
    if chrome
        && visible.iter().all(|&(x, symbol)| {
            symbol == EDGE[0] || (EDGE[1..].contains(&symbol) && fence_glyph(buf, x, y))
        })
    {
        return None;
    }
    // A fence's top rule carries its language label, painted in the border color.
    if chrome && fence_glyph(buf, first_x, y) && first_symbol == EDGE[1] {
        return None;
    }
    let mut lo = first_x.min(content_start);
    let is_chrome = |x: u16, symbol: &str| {
        (CHROME_GLYPHS.contains(&symbol)
            || (BORDER_GLYPHS.contains(&symbol) && x < chat.x.saturating_add(border))
            // A fence's leading rail opens the row: content quoting a rail,
            // in whatever color, never does.
            || (x == first_x && fence_glyph(buf, x, y))
            || (x == chat.x && MARKER_GLYPHS.contains(&symbol)))
            && is_blank(buf, x.saturating_add(1), y)
    };
    if chrome && is_chrome(first_x, first_symbol) {
        // Skip nested borders (`│ │ `), then a diff row's gutter; a header ends on its arrow.
        let run = visible
            .iter()
            .take_while(|(x, symbol)| is_chrome(*x, symbol));
        let (end, last) = run
            .last()
            .map_or((first_x, first_symbol), |&(x, symbol)| (x, symbol));
        let gutter = if BORDER_GLYPHS.contains(&last) {
            gutter
        } else {
            0
        };
        lo = end.saturating_add(CHROME_WIDTH).saturating_add(gutter);
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
    // The fence's closing rail ends the row: content quoting a rail, in
    // whatever color, never does.
    if x1 == last_x && x1 > x0 && closing_rail(buf, x1, y, boxed) {
        x1 -= 1;
        while x1 > x0 && is_blank(buf, x1, y) {
            x1 -= 1;
        }
        if is_blank(buf, x1, y) {
            return None;
        }
    }
    (x0 <= x1).then_some(Row {
        y,
        lo: lo.min(last_x),
        hi: content_end(buf, first_x, last_x, y, boxed),
        x0,
        x1,
    })
}

/// A blank or rail-only row under the selection: an empty content row.
fn blank_row(y: u16, content_start: u16, x0: u16, x1: u16, copy_blank_rows: bool) -> Option<Row> {
    if !copy_blank_rows {
        return None;
    }
    let x0 = x0.max(content_start);
    (x0 <= x1).then_some(Row {
        y,
        lo: content_start,
        hi: content_start,
        x0,
        x1: x0,
    })
}

/// A box glyph: a frame border paints it, code quoting the glyph does not.
fn fence_glyph(buf: &Buffer, x: u16, y: u16, boxed: bool) -> bool {
    buf.cell((x, y)).is_some_and(|cell| {
        (EDGE.contains(&cell.symbol()) || cell.symbol() == RAIL)
            && frame_color(cell.style().fg, boxed)
    })
}

/// The fence color always frames; the warning color only on a row its box glyphs bracket.
fn frame_color(fg: Option<Color>, boxed: bool) -> bool {
    fg == Some(theme::md_fence_border()) || (boxed && fg == Some(theme::warning()))
}

/// A row of the startup Warnings box: it opens and closes on a warning-colored box glyph.
fn warning_boxed(buf: &Buffer, visible: &[(u16, &str)], y: u16) -> bool {
    let warning_box = |&(x, symbol): &(u16, &str)| {
        (EDGE.contains(&symbol) || symbol == RAIL)
            && buf
                .cell((x, y))
                .is_some_and(|cell| cell.style().fg == Some(theme::warning()))
    };
    visible.first().is_some_and(warning_box) && visible.last().is_some_and(warning_box)
}

/// The rail glyph at `x` closes its row: preceded by a blank column.
fn closing_rail(buf: &Buffer, x: u16, y: u16, boxed: bool) -> bool {
    buf.cell((x, y)).is_some_and(|cell| {
        cell.symbol() == RAIL
            && frame_color(cell.style().fg, boxed)
            && is_blank(buf, x.saturating_sub(1), y)
    })
}

/// The row's last content column: past the blank and rail that close it.
fn content_end(buf: &Buffer, first_x: u16, last_x: u16, y: u16, boxed: bool) -> u16 {
    let mut end = last_x;
    if closing_rail(buf, end, y, boxed) {
        end -= 1;
        while end > first_x && is_blank(buf, end, y) {
            end -= 1;
        }
    }
    end
}

#[cfg(test)]
mod tests;
