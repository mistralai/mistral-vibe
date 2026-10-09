//! Shared frame of every bottom app, which keeps all its text selectable.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::Frame;

use super::{bottom_bar, selection, theme, todo};
use crate::app::App;
use crate::mouse::{self, MouseTarget};
use crate::selection::RegionId;

/// The two border columns plus the `padding: 0 1` every bottom-app box uses.
const BORDER_WIDTH: u16 = 4;

/// Which bottom app replaces the input box (Python's `BottomApp`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Approval,
    Question,
    Voice,
    ProxySetup,
    Theme,
    Model,
    LogLevel,
    Thinking,
    Resume,
    Rewind,
    Mcp,
    Plugins,
    McpOAuth,
    ConnectorAuth,
    RemoteProject,
}

impl Kind {
    /// The mouse target of the whole box; apps with clickable rows register their own on top.
    fn target(self) -> MouseTarget {
        match self {
            Kind::Approval => MouseTarget::Approval,
            Kind::Question => MouseTarget::Question,
            Kind::RemoteProject => MouseTarget::RemoteProject,
            Kind::ProxySetup => MouseTarget::ProxySetup,
            _ => MouseTarget::BottomApp,
        }
    }
}

/// Draw the transcript, loading row, todo row and bottom bar around the
/// `box_height` rows of the `kind` box that `draw_box` paints.
pub(crate) fn draw(
    app: &mut App,
    f: &mut Frame,
    area: Rect,
    box_height: u16,
    kind: Kind,
    draw_box: impl FnOnce(&mut App, &mut Frame, Rect),
) {
    f.buffer_mut().set_style(area, theme::screen_style());
    let [transcript, loading, box_area, bar, todo_row] = chunks(app, area, box_height);
    // The input box has no focus while an app replaces it.
    crate::selection::blur_composer(app);
    // A box selection is screen-anchored: another app, or a box that moved or
    // resized, would put new text under it, so drop it before painting.
    if app.view.bottom_app.replace((kind, box_area)) != Some((kind, box_area)) {
        crate::selection::clear_region(app, RegionId::BottomApp);
    }
    selection::draw_transcript(app, f, transcript);
    super::draw_loading_area(app, f, loading);
    selection::loading_region(app, f, loading);
    mouse::register_region(app, box_area, kind.target());
    let content = content(box_area);
    app.view.bottom_app_selection_region = selection::frame_region(content);
    app.view.bottom_app_selection_chrome.clear();
    app.view.bottom_app_selection_folds = crate::selection::Folds::default();
    draw_box(app, f, box_area);
    for track in mouse::scrollbar_tracks(app, content) {
        let cells = (track.y..track.bottom()).map(|y| (y, track.x, track.right() - 1));
        app.view.bottom_app_selection_chrome.extend(cells);
    }
    selection::overlay_region(app, f, RegionId::BottomApp);
    todo::draw_row(app, f, todo_row);
    bottom_bar::draw(app, f, bar);
}

/// The text rectangle inside the box border and padding (Python selects
/// everything its widgets paint, but never the container's border).
pub(crate) fn content(area: Rect) -> Rect {
    Rect {
        x: area.x.saturating_add(BORDER_WIDTH / 2),
        y: area.y.saturating_add(1),
        width: area.width.saturating_sub(BORDER_WIDTH),
        height: area.height.saturating_sub(2),
    }
}

/// `[transcript, loading, box, bottom_bar, todo_row]`: the todo row sits above
/// the box, but last here so the shared indices hold.
fn chunks(app: &App, area: Rect, box_height: u16) -> [Rect; 5] {
    let chunks = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(super::loading_height(app)),
        Constraint::Length(todo::row_height(app)),
        Constraint::Length(box_height),
        Constraint::Length(1),
    ])
    .split(area);
    [chunks[0], chunks[1], chunks[3], chunks[4], chunks[2]]
}
