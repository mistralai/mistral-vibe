//! Vertical scrollbars with eighth-block thumb edges.

mod state;

pub use state::{Side, State};

use std::sync::OnceLock;

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::mouse::MouseTarget;
use crate::terminal_detect;
use crate::tmux;

// Eighths per row: thumb ends are placed to the eighth.
const EIGHTHS: i64 = 8;
// Eighth blocks growing from the bottom, for the thumb's top edge.
const LOWER: [&str; 7] = ["▇", "▆", "▅", "▄", "▃", "▂", "▁"];
// Eighth blocks growing from the top, for the thumb's bottom edge.
const UPPER: [&str; 7] = ["▔", "🮂", "🮃", "▀", "🮄", "🮅", "🮆"];
// The nearest standard blocks, where the terminal leaves Symbols for Legacy Computing to the font.
const UPPER_STANDARD: [&str; 7] = ["▔", "▔", "▀", "▀", "▀", "▀", "█"];

/// How the scrollbar is drawn in the host terminal.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Look {
    /// Foreground glyphs ending in these upper eighths; padding fill copies only the parent's background.
    Glyphs(&'static [&'static str; 7]),
    /// Whole reverse-video cells, for Terminal.app: it draws block glyphs short of the cell and never fills its padding.
    Cells,
}

impl Look {
    /// The thumb body's symbol and whether it is reversed.
    fn body(self) -> (&'static str, bool) {
        match self {
            Look::Glyphs(_) => ("█", false),
            Look::Cells => (" ", true),
        }
    }

    /// A thumb end in eighths; whole cells round it to the nearest row, which leaves no partial glyphs.
    fn snap(self, eighths: i64) -> i64 {
        match self {
            Look::Glyphs(_) => eighths,
            Look::Cells => (eighths + EIGHTHS / 2) / EIGHTHS * EIGHTHS,
        }
    }
}

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
    paint(f, area, virtual_size, window_size, position, look());
}

/// The host terminal's `Look`, detected once; inside tmux, which hides `TERM_PROGRAM`, from the terminal's XTVERSION reply.
fn look() -> Look {
    static LOOK: OnceLock<Look> = OnceLock::new();
    *LOOK.get_or_init(|| match tmux::client_termtype() {
        Some(reply) => look_for_reply(&reply),
        None => look_for(terminal_detect::detect(), terminal_detect::is_rex()),
    })
}

/// The `Look` for an XTVERSION reply; no reply gets whole cells, which every terminal draws, Terminal.app included.
fn look_for_reply(reply: &str) -> Look {
    const NATIVE: [&str; 4] = ["ghostty ", "kitty(", "WezTerm ", "rex "];
    if reply.is_empty() {
        Look::Cells
    } else if NATIVE.iter().any(|name| reply.starts_with(name)) {
        Look::Glyphs(&UPPER)
    } else {
        Look::Glyphs(&UPPER_STANDARD)
    }
}

/// The `Look` for a `terminal_detect` value; `rex` marks Rex, which has none.
fn look_for(terminal: &str, rex: bool) -> Look {
    match terminal {
        terminal_detect::APPLE_TERMINAL => Look::Cells,
        terminal_detect::GHOSTTY | terminal_detect::KITTY | terminal_detect::WEZTERM => {
            Look::Glyphs(&UPPER)
        }
        _ if rex => Look::Glyphs(&UPPER),
        _ => Look::Glyphs(&UPPER_STANDARD),
    }
}

/// Paint the scrollbar into a 1-column `area` (content, viewport, offset).
fn paint(
    f: &mut Frame,
    area: Rect,
    virtual_size: usize,
    window_size: usize,
    position: usize,
    look: Look,
) {
    if area.width == 0 || area.height == 0 {
        return;
    }
    let bar = theme::scrollbar();
    let size = area.height as f64;
    let virtual_size = virtual_size as f64;
    let window_size = window_size as f64;
    let x = area.x;
    let buf = f.buffer_mut();

    // Cells keep the parent's background, so padding fill copies only that; the track is blank.
    for row in 0..area.height {
        if let Some(cell) = buf.cell_mut((x, area.y + row)) {
            cell.set_symbol(" ");
            cell.modifier = Modifier::empty();
        }
    }
    if window_size <= 0.0 || virtual_size <= 0.0 || size == virtual_size {
        return;
    }
    let paint = |buf: &mut ratatui::buffer::Buffer, row: u16, sym: &str, reverse: bool| {
        if let Some(cell) = buf.cell_mut((x, area.y + row)) {
            cell.set_symbol(sym).set_fg(bar);
            cell.modifier.set(Modifier::REVERSED, reverse);
        }
    };

    let eighths = EIGHTHS as f64;
    let bar_ratio = virtual_size / size;
    let thumb_size = (window_size / bar_ratio).max(1.0);
    let position_ratio = position as f64 / (virtual_size - window_size);
    let position = (size - thumb_size) * position_ratio;

    let start = (position * eighths) as i64;
    let end = start + (thumb_size * eighths).ceil() as i64;
    let (start, end) = (look.snap(start).max(0), look.snap(end).max(0));
    let (start_index, start_bar) = (start / EIGHTHS, (start % EIGHTHS) as usize);
    let (end_index, end_bar) = (end / EIGHTHS, (end % EIGHTHS) as usize);

    let (body, reversed) = look.body();
    for row in start_index..end_index.min(i64::from(area.height)) {
        paint(buf, row as u16, body, reversed);
    }

    // Head and tail use partial glyphs for sub-cell positioning.
    if start_index < i64::from(area.height) && start_bar > 0 {
        paint(buf, start_index as u16, LOWER[start_bar - 1], false);
    }
    if let Look::Glyphs(upper) = look {
        if end_index < i64::from(area.height) && end_bar > 0 {
            paint(buf, end_index as u16, upper[end_bar - 1], false);
        }
    }
}

#[cfg(test)]
mod tests;
