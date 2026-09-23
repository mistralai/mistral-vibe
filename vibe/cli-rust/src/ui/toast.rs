//! Warning/error toast overlay (Python Textual `Toast`).

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::widgets::Clear;
use ratatui::Frame;

use super::{selection, theme};
use crate::app::{App, ToastSeverity};
use crate::selection::{Region, RegionId};
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
    app.view.toast_text_areas.clear();
    if app.overlays.toasts.is_empty() {
        reconcile_selection(app, f);
        return;
    }
    if let Some(toast) = app.overlays.toasts.back_mut() {
        toast.until.get_or_insert(now + toast.duration);
    }
    let area = f.area();
    let box_width = BOX_WIDTH.min(area.width.saturating_sub(RIGHT_MARGIN));
    if box_width < MIN_BOX_WIDTH {
        reconcile_selection(app, f);
        return;
    }
    let text_width = (box_width - 3) as usize;
    let x = area.width - RIGHT_MARGIN - box_width;
    let mut bottom = anchor.y;
    let mut text_areas = Vec::new();
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
        text_areas.push((
            toast.id,
            Rect::new(x + 2, y + 1, text_width as u16, lines.len() as u16),
        ));
        bottom = y;
    }
    // The rack paints newest first; keep the published rects in stack order.
    text_areas.reverse();
    app.view.toast_text_areas = text_areas;
    // The toasts paint last, so their text takes the pointer from the widgets
    // below; a selection is anchored in one toast, never across two.
    for index in 0..app.view.toast_text_areas.len() {
        let (_, area) = app.view.toast_text_areas[index];
        crate::mouse::register_region(app, area, crate::mouse::MouseTarget::Toast);
    }
    reconcile_selection(app, f);
}

/// Publish the text region of the toast at `at`, returning the toast it belongs
/// to so a gesture can record its owner.
pub fn claim_region(app: &mut App, at: (u16, u16)) -> Option<u64> {
    let (id, area) = app
        .view
        .toast_text_areas
        .iter()
        .copied()
        .find(|(_, area)| contains(*area, at))?;
    app.view.toast_selection_region = region(area);
    Some(id)
}

/// Keep a toast selection on the toast that owns it: republish that toast's
/// current rectangle so the highlight follows the rack, and drop the selection
/// once the toast stops painting (expired, evicted, or pushed off screen).
fn reconcile_selection(app: &mut App, f: &mut Frame) {
    let Some(RegionId::Toast(id)) = app.selection.region.as_ref().map(|sel| sel.owner) else {
        return;
    };
    let Some((_, area)) = app
        .view
        .toast_text_areas
        .iter()
        .copied()
        .find(|(painted, _)| *painted == id)
    else {
        app.view.toast_selection_region = Region::default();
        crate::selection::clear_region(app, RegionId::Toast(id));
        return;
    };
    app.view.toast_selection_region = region(area);
    selection::overlay_region(app, f, RegionId::Toast(id));
}

/// The selectable region one toast's text rows form. Its rows are all text, so
/// the region owns no chrome, and it keeps the cell under the pointer like the
/// transcript: Python's `WordSelectScreen` owns selection for everything the
/// screen paints, toasts included, so multi-click behaves the same on both.
fn region(area: Rect) -> Region {
    Region {
        area,
        top: i32::from(area.y),
        scrollbar: false,
        end_exclusive: false,
        document: false,
        ..Region::default()
    }
}

fn contains(area: Rect, at: (u16, u16)) -> bool {
    at.0 >= area.x && at.0 < area.right() && at.1 >= area.y && at.1 < area.bottom()
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
