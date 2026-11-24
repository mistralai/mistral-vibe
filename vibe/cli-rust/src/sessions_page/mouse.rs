//! The page's mouse: the folders column picks a folder, the list highlights and opens, the preview scrolls and folds, and drags select text.

use std::sync::Arc;

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};

use super::{preview_view, Column};
use crate::app::App;
use crate::mouse::option_gesture;
use crate::option_list::{MultiAction, OptionList, OptionView};
use crate::selection::{self, RegionId, Release};
use crate::server::Client;

/// Rows one wheel notch scrolls.
const WHEEL: usize = 2;

fn in_folders(app: &App, at: (u16, u16)) -> bool {
    app.sessions_page.folders_shown && app.sessions_page.folders_area.contains(at.into())
}

/// A click on a folder lists its sessions; on a row it highlights, then opens.
pub fn dispatch(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    // The rename modal owns the mouse while it is open.
    if app.sessions_page.rename.is_some() {
        return super::rename::mouse(app, event);
    }
    let at = (event.column, event.row);
    if preview_view::contains(app, at) {
        return preview_click(app, event);
    }
    if !in_folders(app, at) {
        if let Some(click) = option_gesture(app, event, RegionId::BottomApp, sessions) {
            super::keys::click(app, client, click);
        }
        return;
    }
    // A switch in flight or a title being edited keeps the folder it shows.
    let page = &app.sessions_page;
    if page.busy.is_some() || page.rename.is_some() {
        return;
    }
    if let Some(option) = app.sessions_page.browse.folders.mouse(event) {
        app.sessions_page.browse.focus = Column::Folders;
        super::folders::pick(app, option);
    }
}

fn sessions(app: &mut App) -> &mut OptionList<MultiAction> {
    &mut app.sessions_page.browse.list
}

/// A click on the preview's conversation folds the entry under it open or
/// shut; a drag there selects its text, as in the conversation.
fn preview_click(app: &mut App, event: MouseEvent) {
    let at = (event.column, event.row);
    match event.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            app.selection.had_selection_at_press = app.selection.region.is_some();
            selection::press_owned(app, at, RegionId::Preview);
        }
        MouseEventKind::Drag(MouseButton::Left) => selection::drag(app, at),
        MouseEventKind::Up(MouseButton::Left) => {
            if !matches!(selection::release(app), Release::RegionClick) {
                return;
            }
            // A link opens, as in the conversation; any other click folds the entry under it.
            if let Some(link) = preview_view::link_at(app, at).cloned() {
                preview_view::open_link(app, &link);
            } else if preview_view::toggle_at(app, event.row) {
                // A toggling click ends the chain, so the next one toggles again.
                app.selection.chain = Default::default();
            }
        }
        _ => {}
    }
}

/// The wheel scrolls the column under the pointer without moving its highlight.
pub fn wheel(app: &mut App, event: MouseEvent, down: bool) {
    // The rename modal owns the mouse: the list and preview behind it stay put.
    // Its one-line title follows the caret, so the wheel has nothing to scroll there.
    if app.sessions_page.rename.is_some() {
        return;
    }
    let at = (event.column, event.row);
    if preview_view::contains(app, at) {
        // The preview's selection is anchored to the screen: the conversation
        // moving under it would leave it on other text.
        selection::clear_region(app, RegionId::Preview);
        return preview_view::scroll(app, !down, preview_view::STEP);
    }
    match in_folders(app, at) {
        true => app.sessions_page.browse.folders.wheel(down, WHEEL),
        false => super::keys::wheel(app, down),
    }
}

/// The list under the pointer, for hover and the scrollbar.
pub fn list(app: &mut App) -> &mut dyn OptionView {
    let over = app
        .view
        .mouse_position
        .is_some_and(|at| in_folders(app, at));
    match over {
        true => &mut app.sessions_page.browse.folders,
        false => &mut app.sessions_page.browse.list,
    }
}
