//! The folders column: which folder the sessions column lists, and which column has the keys.

use super::{preview, Column};
use crate::app::App;

/// From this page width the folders column shows beside the list.
pub const WIDE: u16 = 110;

/// The terminal is `width` columns wide: the folders column comes or goes with
/// the room the page gets beside the plan panel and debug console, and the list
/// is laid out again for it (flat beside it, grouped without).
pub fn resized(app: &mut App, width: u16) {
    fitted(app, crate::ui::session_screen_width(app, width));
}

/// The page is `width` columns wide: the folders column comes or goes.
pub fn fitted(app: &mut App, width: u16) {
    let shown = width >= WIDE;
    let page = &mut app.sessions_page;
    if page.folders_shown == shown {
        return;
    }
    page.folders_shown = shown;
    if !shown {
        page.browse.focus = Column::Sessions;
    }
    super::relayout(app);
}

/// Whether the keys go to the folders column: only while it is shown.
pub fn focused(app: &App) -> bool {
    app.sessions_page.folders_shown && app.sessions_page.browse.focus == Column::Folders
}

/// ← in the sessions column: the folders take the keys, when shown.
pub fn focus(app: &mut App, column: Column) {
    if column == Column::Sessions || app.sessions_page.folders_shown {
        app.sessions_page.browse.focus = column;
    }
}

/// ↑↓ in the folders column: the next folder, wrapping, lists at once.
pub fn step(app: &mut App, down: bool) {
    let len = app.sessions_page.view.folders.len() + 1;
    let list = &mut app.sessions_page.browse.folders;
    list.step(down, len, true, |_| true);
    let option = list.selected();
    pick(app, option);
}

/// List the folder at `option` (0 is All), its newest session highlighted.
pub fn pick(app: &mut App, option: usize) {
    let page = &mut app.sessions_page;
    let key = option
        .checked_sub(1)
        .and_then(|index| page.view.folders.get(index))
        .map(|folder| folder.key.clone());
    if key == page.browse.folder {
        return;
    }
    page.browse.folder = key;
    page.browse.list.reset();
    super::relayout_on(app, None);
    app.sessions_page.browse.list.select(0);
    preview::request(app);
}
