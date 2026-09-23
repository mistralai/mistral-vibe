//! Trust gate layout and painting: build the rows, paint them and the chrome.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::trust_folders::PADDING_X;
use super::trust_folders_layout::Layout;
use super::trust_folders_text::Row;
use crate::app::App;

/// Paint the rows and report the cells that belong to no widget, which are the
/// centering padding around each row and the margins between two options.
pub(super) fn paint(
    app: &mut App,
    f: &mut Frame,
    dialog: Rect,
    content_width: usize,
    layout: &Layout,
) -> Vec<(u16, u16, u16)> {
    let left = dialog.x + 1 + PADDING_X;
    let right = left + content_width as u16 - 1;
    let top = dialog.y + 2;
    let visible = (dialog.height as usize).saturating_sub(4);
    let painted = layout.rows.len().min(visible);
    let mut chrome: Vec<(u16, u16, u16)> = (dialog.y + 1..dialog.bottom() - 1)
        .filter(|y| *y < top || *y >= top + painted as u16)
        .map(|y| (y, left, right))
        .collect();
    for (index, row) in layout.rows.iter().take(visible).enumerate() {
        let width = match index < layout.scroll_rows {
            true => layout.scroll_width,
            false => content_width,
        };
        let y = top + index as u16;
        let segments = paint_centered_row(f.buffer_mut(), row, left, width, y);
        let lo = segments.iter().find(|(.., filled)| *filled).map(|s| s.0);
        let hi = segments
            .iter()
            .rev()
            .find(|(.., filled)| *filled)
            .map(|s| s.1);
        let (Some(lo), Some(hi)) = (lo, hi) else {
            chrome.push((y, left, right));
            continue;
        };
        if lo > left {
            chrome.push((y, left, lo - 1));
        }
        if hi < right {
            chrome.push((y, hi + 1, right));
        }
        chrome.extend(
            segments
                .iter()
                .filter(|&&(x0, x1, filled)| !filled && x0 > lo && x1 < hi)
                .map(|&(x0, x1, _)| (y, x0, x1)),
        );
    }
    let Some(virtual_rows) = layout.virtual_rows else {
        return chrome;
    };
    let scroll_rows = (layout.scroll_rows as u16).min(visible as u16);
    if scroll_rows == 0 {
        return chrome;
    }
    let bar = Rect::new(left + layout.scroll_width as u16, top, 1, scroll_rows);
    app.view.selection_scrollbar.update_large(
        bar,
        virtual_rows,
        usize::from(scroll_rows),
        layout.scroll,
    );
    super::scrollbar::draw(
        app,
        f,
        crate::mouse::MouseTarget::Trust,
        bar,
        virtual_rows as u16,
        scroll_rows,
        layout.scroll as u16,
    );
    chrome
}

/// Paint one centered content row and return its occupied segments.
pub(super) fn paint_centered_row(
    buffer: &mut Buffer,
    row: &Row,
    left: u16,
    width: usize,
    y: u16,
) -> Vec<(u16, u16, bool)> {
    let mut x = row.left(left, width);
    let mut segments = Vec::new();
    for (text, style) in &row.spans {
        buffer.set_string(x, y, text, *style);
        let painted_width = text.width() as u16;
        if painted_width > 0 {
            segments.push((x, x + painted_width - 1, !text.trim().is_empty()));
        }
        x += painted_width;
    }
    segments
}
