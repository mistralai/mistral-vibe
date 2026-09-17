//! Warning/error toast overlay (Python Textual `Toast`).

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::widgets::Clear;
use ratatui::Frame;

use super::theme;
use crate::app::{App, ToastSeverity};

// Textual `Toast { width: 60 }` renders as 59 cells inside the ToastRack, docked
// bottom-right with a 2-column right margin. Interior text is `width - border(1) -
// padding(1+1)`.
const BOX_WIDTH: u16 = 59;
const RIGHT_MARGIN: u16 = 2;
const TEXT_WIDTH: usize = (BOX_WIDTH - 3) as usize;

/// Overlay the active toast just above the input box, right-aligned.
pub fn draw(app: &mut App, f: &mut Frame, input_area: Rect) {
    let Some(toast) = &app.overlays.toast else {
        return;
    };
    if std::time::Instant::now() >= toast.until {
        app.overlays.toast = None;
        return;
    }
    let area = f.area();
    if area.width < BOX_WIDTH + RIGHT_MARGIN {
        return;
    }
    let lines = wrap(&toast.text, TEXT_WIDTH);
    let height = lines.len() as u16 + 2; // padding-top + lines + padding-bottom
    if input_area.y < height {
        return;
    }
    let border = match toast.severity {
        ToastSeverity::Information => theme::success(),
        ToastSeverity::Warning => theme::warning(),
        ToastSeverity::Error => theme::error(),
    };
    let x = area.width - RIGHT_MARGIN - BOX_WIDTH;
    let y = input_area.y - height;
    let box_area = Rect::new(x, y, BOX_WIDTH, height);

    f.render_widget(Clear, box_area);
    let background = theme::toast_background();
    let bg = Style::default().fg(theme::foreground()).bg(background);
    f.buffer_mut().set_style(box_area, bg);

    let buf = f.buffer_mut();
    for row in 0..height {
        if let Some(cell) = buf.cell_mut((x, y + row)) {
            cell.set_symbol("▌").set_fg(border).set_bg(background);
        }
    }
    for (i, line) in lines.iter().enumerate() {
        buf.set_string(x + 2, y + 1 + i as u16, line, bg);
    }
}

/// Greedy word wrap, matching Textual's default fold for plain content.
fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let mut cur = String::new();
    for word in text.split(' ') {
        if cur.is_empty() {
            cur = word.to_string();
        } else if cur.chars().count() + 1 + word.chars().count() <= width {
            cur.push(' ');
            cur.push_str(word);
        } else {
            lines.push(std::mem::take(&mut cur));
            cur = word.to_string();
        }
    }
    if !cur.is_empty() || lines.is_empty() {
        lines.push(cur);
    }
    lines
}
