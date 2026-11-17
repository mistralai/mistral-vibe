//! The highlighted loop's instructions: wrapped in full, scrolled on their own, a new loop starting at the top.

use ratatui::layout::Rect;
use ratatui::Frame;

use crate::app::App;
use crate::loops::{schedule, selected};
use crate::mouse::MouseTarget;
use crate::server::ScheduledLoop;
use crate::ui::{scrollbar, theme, visual_lines};

const NONE: &str = "No instructions.";

pub(super) fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    if area.height == 0 || area.width < 2 {
        return;
    }
    let Some(scheduled) = selected(app) else {
        return;
    };
    let id = scheduled.id.clone();
    let instructions = shown(scheduled).to_owned();
    let style = theme::muted_style().bg(theme::background());
    let text = instructions.as_str();
    let mut lines = wrap(text, style, usize::from(area.width));
    let overflows = lines.len() > usize::from(area.height);
    if overflows {
        lines = wrap(text, style, usize::from(area.width - 2));
    }
    let details = &mut app.loops.details;
    if details.shown.as_ref() != Some(&id) {
        details.shown = Some(id);
        details.scroll = 0;
    }
    details.lines = lines.len();
    details.visible = usize::from(area.height);
    details.scroll_by(false, 0);
    let offset = details.scroll;
    crate::mouse::register_region(app, area, MouseTarget::LoopsDetails);
    for (row, line) in lines
        .iter()
        .skip(offset)
        .take(usize::from(area.height))
        .enumerate()
    {
        let spans = visual_lines::merge(line);
        let y = area.y + row as u16;
        f.buffer_mut()
            .set_line(area.x, y, &ratatui::text::Line::from(spans), area.width);
    }
    if overflows {
        let bar = Rect::new(area.right() - 1, area.y, 1, area.height);
        let (total, visible) = (lines.len() as u16, area.height);
        scrollbar::draw(
            app,
            f,
            MouseTarget::LoopsDetails,
            bar,
            total,
            visible,
            offset as u16,
        );
    }
}

/// Lines `scheduled`'s instructions take wrapped at `width`.
pub(super) fn height(scheduled: &ScheduledLoop, width: u16) -> u16 {
    let style = theme::muted_style();
    wrap(shown(scheduled), style, usize::from(width)).len() as u16
}

fn shown(scheduled: &ScheduledLoop) -> &str {
    match schedule::instructions(scheduled) {
        "" => NONE,
        instructions => instructions,
    }
}

fn wrap(text: &str, style: ratatui::style::Style, width: usize) -> Vec<Vec<visual_lines::Sc>> {
    visual_lines::wrap(&visual_lines::text(text, style), width)
}
