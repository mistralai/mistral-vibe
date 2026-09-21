//! Warning/error toast overlay (Python Textual `Toast`).

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::widgets::Clear;
use ratatui::Frame;

use super::theme;
use crate::app::{App, ToastSeverity};
use crate::utils::text::wrap_hard;

// Textual `Toast { width: 60 }` renders as 59 cells inside the ToastRack, docked
// bottom-right with a 2-column right margin. Interior text is `width - border(1) -
// padding(1+1)`.
const BOX_WIDTH: u16 = 59;
const RIGHT_MARGIN: u16 = 2;
const MIN_BOX_WIDTH: u16 = 10;

/// Overlay the active toast stack right-aligned above `anchor.y`, newest at the
/// bottom (Python `ToastRack`). Drops expired toasts and clamps to the width.
pub fn draw(app: &mut App, f: &mut Frame, anchor: Rect) {
    let now = std::time::Instant::now();
    app.overlays
        .toasts
        .retain(|toast| toast.until.is_none_or(|until| now < until));
    if app.overlays.toasts.is_empty() {
        return;
    }
    if let Some(toast) = app.overlays.toasts.back_mut() {
        toast.until.get_or_insert(now + toast.duration);
    }
    let area = f.area();
    let box_width = BOX_WIDTH.min(area.width.saturating_sub(RIGHT_MARGIN));
    if box_width < MIN_BOX_WIDTH {
        return;
    }
    let text_width = (box_width - 3) as usize;
    let x = area.width - RIGHT_MARGIN - box_width;
    let mut bottom = anchor.y;
    for toast in app.overlays.toasts.iter_mut().rev() {
        let mut lines = wrap_hard(&toast.text, text_width);
        let omitted = toast.omitted;
        if omitted > 0 {
            let noun = if omitted == 1 {
                "notification"
            } else {
                "notifications"
            };
            let message = format!("{omitted} earlier {noun} omitted");
            lines.extend(wrap_hard(&message, text_width));
        }
        let height = lines.len() as u16 + 2; // padding-top + lines + padding-bottom
        if bottom < height {
            break;
        }
        toast.until.get_or_insert(now + toast.duration);
        let y = bottom - height;
        draw_box(f, toast, Rect::new(x, y, box_width, height), &lines);
        bottom = y;
    }
}

fn draw_box(f: &mut Frame, toast: &crate::app::Toast, box_area: Rect, lines: &[String]) {
    let border = match toast.severity {
        ToastSeverity::Information => theme::success(),
        ToastSeverity::Warning => theme::warning(),
        ToastSeverity::Error => theme::error(),
    };
    f.render_widget(Clear, box_area);
    let background = theme::toast_background();
    let bg = Style::default().fg(theme::foreground()).bg(background);
    f.buffer_mut().set_style(box_area, bg);

    let buf = f.buffer_mut();
    for row in 0..box_area.height {
        if let Some(cell) = buf.cell_mut((box_area.x, box_area.y + row)) {
            cell.set_symbol("▌").set_fg(border).set_bg(background);
        }
    }
    for (i, line) in lines.iter().enumerate() {
        buf.set_string(box_area.x + 2, box_area.y + 1 + i as u16, line, bg);
    }
}
