//! The loading area: the narrator, the loading line, the rail, the inline notice and the feedback bar, then a wrapped notice block.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::Frame;

use super::{feedback_bar, loading, narrator, notice, rail};
use crate::app::App;

/// The loading-area row, mounted on every screen that keeps it: the
/// narrator status, the loading spinner, the rail, the inline notice and the
/// feedback bar, laid out left to right.
pub(crate) fn draw_loading_area(app: &mut App, f: &mut Frame, area: Rect) {
    // A squeezed area keeps the loading row: the block only gets the rows beyond it.
    let block = notice_block(app, area.width).min(area.height.saturating_sub(2));
    let [area, block_area] =
        Layout::vertical([Constraint::Fill(1), Constraint::Length(block)]).areas(area);
    // Beside a rail, a one-row notice sits on the row above it instead of squeezing its items off.
    let above_rail = block == 0 && area.height > 1 && !crate::rail::items(app).is_empty();
    let notice_width = match block > 0 || above_rail {
        true => 0,
        false => notice::width(app),
    };
    let feedback_width = feedback_bar::width(app);
    let narrator_width = narrator::width(app);
    let others = narrator_width + notice_width + feedback_width + u16::from(feedback_width > 0);
    let rail_width = rail::width_beside(app, area.width, others);
    // The narrator row is laid out before the loading spinner; zero width
    // while idle keeps the split identical to before.
    let loading_chunks = Layout::horizontal([
        Constraint::Length(narrator_width),
        Constraint::Fill(1),
        Constraint::Length(rail_width),
        Constraint::Length(notice_width),
        Constraint::Length(u16::from(feedback_width > 0)),
        Constraint::Length(feedback_width),
    ])
    .split(area);

    narrator::draw(app, f, loading_chunks[0]);
    loading::draw(app, f, loading_chunks[1]);
    rail::draw_in(app, f, loading_chunks[2]);
    feedback_bar::draw(app, f, loading_chunks[5]);
    if block > 0 {
        notice::draw(app, f, block_area, true);
    } else if above_rail {
        let width = notice::width(app).min(area.width);
        let row = Rect {
            x: area.right() - width,
            y: area.bottom() - 2,
            width,
            height: 1,
        };
        notice::draw(app, f, row, false);
    } else {
        notice::draw(app, f, loading_chunks[3], false);
    }
}

/// Rows of the wrapped-failure block under the loading row: 0 when the notice fits beside the narrator and feedback.
fn notice_block(app: &App, width: u16) -> u16 {
    let feedback = feedback_bar::width(app);
    let others = narrator::width(app) + feedback + u16::from(feedback > 0);
    notice::block_rows(app, width.saturating_sub(others), width)
}

/// A failed startup or a wrapped loading line grows the area under its padding row; a wrapped failure adds its block below.
pub(crate) fn loading_height(app: &App, width: u16) -> u16 {
    let base = if app.view.transcript.is_empty() { 3 } else { 2 };
    let line = loading_line_width(app, width);
    let rows = loading::failure::rows(app, width).max(loading::rows(app, line));
    base.max(rows.saturating_add(1)) + notice_block(app, width)
}

/// The loading line's share of the row once the narrator, rail, notice and feedback take theirs.
fn loading_line_width(app: &App, width: u16) -> u16 {
    let above_rail = !crate::rail::items(app).is_empty();
    let notice = match notice_block(app, width) == 0 && !above_rail {
        true => notice::width(app),
        false => 0,
    };
    let feedback = feedback_bar::width(app);
    let others = narrator::width(app) + notice + feedback + u16::from(feedback > 0);
    width.saturating_sub(others + rail::width_beside(app, width, others))
}
