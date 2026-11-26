//! The jump-to-bottom chip shown over a scrolled-up transcript.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::theme;
use crate::app::App;
use crate::mouse::{self, MouseTarget};

const LABEL: &str = " ↓ Jump to bottom ";

/// Paint the chip centered on the last row of `area` while the transcript is scrolled up.
/// Hidden without mouse capture: no click would reach it.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let width = LABEL.width() as u16;
    let clickable =
        crate::terminal::captures_mouse(app.session.startup_config.enable_mouse_capture);
    if !clickable || app.view.scroll_target == 0 || area.height < 2 || area.width < width + 2 {
        return;
    }
    let chip = Rect {
        x: area.x + (area.width - width) / 2,
        y: area.bottom() - 1,
        width,
        height: 1,
    };
    // Drop the modifiers of the cells underneath (dim table rows, bold headings).
    let style = Style::default()
        .fg(theme::foreground())
        .bg(theme::chip_background())
        .remove_modifier(Modifier::all());
    f.buffer_mut().set_string(chip.x, chip.y, LABEL, style);
    app.view
        .selection_chrome
        .push((chip.y, chip.x, chip.right() - 1));
    mouse::register_region(app, chip, MouseTarget::JumpToBottom);
}
