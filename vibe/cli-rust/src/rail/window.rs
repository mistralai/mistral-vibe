//! Keys of the tasks, PR, goal or plan window, its text scroll, and closing whichever window is open.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent};

use super::{enter, is_toggle_key, open_item, window, Item, Scroll};
use crate::app::App;
use crate::focus::Focus;
use crate::server::Client;

/// Open the goal or plan window at `scroll`.
pub(super) fn show_text(app: &mut App, item: Item, scroll: Scroll) {
    app.rail.window = Some(item);
    app.rail.scroll = scroll;
}

/// Keys of the open tasks, PR, goal or plan window; an Esc the window leaves closes it back to the rail.
pub fn handle_window_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    if window(app) == Some(Item::Tasks) && crate::tasks_panel::handle_key(app, client, key) {
        return;
    }
    if key.code == KeyCode::Esc {
        close_text(app);
        return;
    }
    match window(app) {
        Some(Item::Prs) => crate::artifacts_modal::handle_key(app, key),
        Some(Item::Goal | Item::Plan) if key.modifiers.is_empty() && scroll_key(app, key.code) => {}
        Some(Item::Goal) => crate::goal::window_key(app, client, &key),
        _ => {}
    }
}

/// ↑↓/jk scroll the goal or plan text one row, clamped to what the last frame could show.
fn scroll_key(app: &mut App, code: KeyCode) -> bool {
    let down = match code {
        KeyCode::Up | KeyCode::Char('k') => false,
        KeyCode::Down | KeyCode::Char('j') => true,
        _ => return false,
    };
    scroll(app, down, 1);
    true
}

/// Move the goal, plan or output text by `rows`, dropping a selection over it; reaching the end follows new output again.
pub fn scroll(app: &mut App, down: bool, rows: usize) {
    let rail = &mut app.rail;
    // The in-progress todo's row and the last page's are known once a frame placed them.
    let before = match rail.scroll {
        Scroll::Row(row) => row.min(rail.scroll_max),
        Scroll::Current | Scroll::End => rail.scroll_top,
    };
    let top = match down {
        true => before.saturating_add(rows).min(rail.scroll_max),
        false => before.saturating_sub(rows),
    };
    // A key that moves nothing keeps following the output or the current todo.
    if top == before {
        return;
    }
    rail.scroll = match down && top == rail.scroll_max {
        true => Scroll::End,
        false => Scroll::Row(top),
    };
    moved(app);
}

/// Scroll by `rows`, as a selection drag past an edge does; returns the rows the text moved.
pub fn scroll_by(app: &mut App, rows: i32) -> i32 {
    let before = app.rail.scroll_top as i32;
    let after = (before + rows).clamp(0, app.rail.scroll_max as i32);
    scroll_to(app, after as usize);
    after - before
}

/// A screen-anchored selection must not stay over moved text; a document-anchored one rides it.
fn moved(app: &mut App) {
    let region = app.view.rail_window_selection_region;
    if region.scroll_target != crate::selection::ScrollTarget::Output {
        crate::selection::clear_region(app, crate::selection::RegionId::RailWindow);
    }
}

/// Put the goal, plan or output text's first row at `top`, as a scrollbar drag does; the last row follows new output again.
pub fn scroll_to(app: &mut App, top: usize) {
    let before = app.rail.scroll_top;
    app.rail.scroll = match top >= app.rail.scroll_max {
        true => Scroll::End,
        false => Scroll::Row(top),
    };
    if top != before {
        moved(app);
    }
}

fn close_text(app: &mut App) {
    app.rail.window = None;
    app.tasks_panel.close_output();
    crate::selection::clear_region(app, crate::selection::RegionId::RailWindow);
}

/// Close the window the keyboard is on, so Ctrl+C never clears a draft it hides; false when none is.
pub fn close_window(app: &mut App) -> bool {
    // The goal window's edit or clear panel goes first, back to the window.
    if app.focus() == Focus::Goal && window(app) == Some(Item::Goal) {
        app.goal.panel = None;
        return true;
    }
    let on_window = app.focus().is_rail_surface();
    open_item(app)
        .filter(|_| on_window)
        .is_some_and(|item| close(app, item))
}

/// Close `item`'s open window back to the rail, letters hidden; false for a child view, which only Esc leaves.
pub(super) fn close(app: &mut App, item: Item) -> bool {
    app.rail.letters = false;
    match item {
        Item::Loops => crate::loops::close(app),
        Item::Tasks | Item::Prs | Item::Goal | Item::Plan => close_text(app),
        Item::Agents if app.subagents.viewed_subagent_id.is_none() => {
            crate::subagents::leave_list(app)
        }
        Item::Agents => return false,
    }
    true
}

/// Close the picker in the box's place, as its Esc would, so a chip click lands; false when it holds input or an answer is due.
pub fn leave_box(app: &mut App, client: &Arc<Client>) -> bool {
    match app.focus() {
        Focus::Composer => return true,
        focus if focus.is_rail_surface() => return true,
        // The loop list is the loops chip's window: the chip or the rail takes over from it.
        Focus::Loops if super::loop_list(app) => return true,
        Focus::Loops if app.loops.form.is_none() => crate::loops::close(app),
        Focus::ThemePicker => crate::theme_picker::cancel(app),
        Focus::ModelPicker => crate::model_picker::cancel(app),
        Focus::ThinkingPicker => crate::thinking_picker::cancel(app),
        Focus::LogLevelPicker => crate::log_level_picker::apply(app, client),
        Focus::Mcp => crate::mcp::close(app),
        Focus::Plugins => crate::plugins::close(app),
        Focus::Skills => crate::skills_browser::close(app),
        Focus::ContextApp => crate::context_app::close(app),
        Focus::Rewind => crate::rewind::quit(app),
        _ => return false,
    }
    true
}

/// Ctrl+X while a picker fills the box under the rail: close the picker like a chip click, then letter the rail; a box that stays open keeps the key.
pub fn enter_from_box(app: &mut App, client: &Arc<Client>, key: &KeyEvent) -> bool {
    let over_box = app.view.bottom_app.is_some() && !super::items(app).is_empty();
    let answering = matches!(app.focus(), Focus::Approval | Focus::Question);
    if !is_toggle_key(key) || !over_box || answering || !leave_box(app, client) {
        return false;
    }
    enter(app);
    true
}
