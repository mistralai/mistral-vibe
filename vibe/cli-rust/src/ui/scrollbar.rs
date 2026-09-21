//! Vertical scrollbars ported from Textual's `ScrollBarRender`.

mod state;

pub use state::State;

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier};
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::mouse::MouseTarget;

// Eighth blocks growing from the bottom; index 7 is a space (no partial cell).
const BARS: [&str; 8] = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", " "];

/// Draw the scrollbar into a 1-column `area` (content, viewport, offset).
pub fn draw(
    app: &mut App,
    f: &mut Frame,
    target: MouseTarget,
    area: Rect,
    virtual_size: u16,
    window_size: u16,
    position: u16,
) {
    draw_large(
        app,
        f,
        target,
        area,
        usize::from(virtual_size),
        usize::from(window_size),
        usize::from(position),
    );
}

/// Draw a scrollbar whose document can exceed `u16` rows.
pub fn draw_large(
    app: &mut App,
    f: &mut Frame,
    target: MouseTarget,
    area: Rect,
    virtual_size: usize,
    window_size: usize,
    position: usize,
) {
    if area.width == 0 || area.height == 0 {
        return;
    }
    if virtual_size > window_size {
        crate::mouse::register_scrollbar(app, target, area, virtual_size, window_size, position);
    }
    // Theme-derived thumb ($scrollbar) and track ($scrollbar-background).
    let bar = theme::scrollbar();
    let back = theme::scrollbar_bg();
    let size = area.height as f64;
    let virtual_size = virtual_size as f64;
    let window_size = window_size as f64;
    let x = area.x;
    let buf = f.buffer_mut();

    let paint = |buf: &mut ratatui::buffer::Buffer,
                 row: u16,
                 sym: &str,
                 fg: Color,
                 bg: Color,
                 reverse: bool| {
        if let Some(cell) = buf.cell_mut((x, area.y + row)) {
            cell.set_symbol(sym).set_fg(fg).set_bg(bg);
            if reverse {
                cell.modifier.insert(Modifier::REVERSED);
            } else {
                cell.modifier.remove(Modifier::REVERSED);
            }
        }
    };

    if window_size <= 0.0 || virtual_size <= 0.0 || size == virtual_size {
        for row in 0..area.height {
            paint(buf, row, " ", back, back, false);
        }
        return;
    }

    let len_bars = BARS.len() as f64;
    let bar_ratio = virtual_size / size;
    let thumb_size = (window_size / bar_ratio).max(1.0);
    let position_ratio = position as f64 / (virtual_size - window_size);
    let position = (size - thumb_size) * position_ratio;

    let start = (position * len_bars) as i64;
    let end = start + (thumb_size * len_bars).ceil() as i64;
    let (start_index, start_bar) = (start.max(0) / 8, (start.max(0) % 8) as usize);
    let (end_index, end_bar) = (end.max(0) / 8, (end.max(0) % 8) as usize);

    for row in 0..area.height {
        let i = row as i64;
        if i >= start_index && i < end_index {
            // Thumb body: a reversed blank painting the bar color over the background.
            paint(buf, row, " ", bar, theme::background(), true);
        } else {
            paint(buf, row, " ", back, back, false);
        }
    }

    // Head and tail use partial glyphs for sub-cell positioning; tail is reversed.
    if start_index < area.height as i64 {
        let glyph = BARS[7 - start_bar];
        if glyph != " " {
            paint(buf, start_index as u16, glyph, bar, back, false);
        }
    }
    if end_index < area.height as i64 {
        let glyph = BARS[7 - end_bar];
        if glyph != " " {
            paint(buf, end_index as u16, glyph, bar, back, true);
        }
    }
}
