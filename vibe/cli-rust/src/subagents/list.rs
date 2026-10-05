//! Status-list state and interaction (Python `SubagentList` + app wiring).

use std::collections::HashSet;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::App;
use crate::server::PublicChildSession;
use crate::server::SessionStatus;
use crate::subagents::is_active;
use crate::subagents::view::{remember_scroll, restore_scroll, schedule_refresh};

/// Python `_MAIN_SESSION_ID`: row 0, the main-conversation row.
pub const MAIN_SESSION_ID: &str = "\u{0}main";
/// Rendered row cap (Python TCSS `#subagent-list { max-height: 7 }`).
pub const MAX_ROWS: usize = 7;

/// Python `SubagentList` navigation state; the rendered rows derive from it.
#[derive(Default)]
pub struct ListState {
    /// Python `_known_session_ids`: every id ever seen, for new-id detection.
    pub known_session_ids: HashSet<String>,
    /// Python `_batch_session_ids`: children held listed until the batch ends.
    pub batch_session_ids: HashSet<String>,
    /// Python `_selected_session_id`: the viewed child, or `None` for main.
    pub selected_session_id: Option<String>,
    /// Keyboard highlight row; 0 is the Main conversation row.
    pub highlighted: usize,
    /// Python `_mouse_session_id`: the row under the mouse, if any.
    pub mouse_session_id: Option<String>,
    /// Whether the list owns keyboard focus (Python widget `has_focus`).
    pub focused: bool,
    /// Wheel-scrolled viewport top, until the highlight moves again (Python
    /// `ScrollView` wheel scrolling vs `scroll_to_highlight`).
    pub free_scroll: Option<usize>,
    /// Viewport top painted by the latest frame, the wheel's base offset.
    pub rendered_scroll: usize,
    /// Rows the list renders, recomputed on every state change (Python options).
    pub rows: Vec<PublicChildSession>,
    /// Painted row rectangles and their row indices, for mouse hit testing.
    pub row_areas: Vec<(ratatui::layout::Rect, usize)>,
    /// Row the mouse button went down on; a click selects it on release.
    pub press_row: Option<usize>,
    /// Painted widget area, used to anchor toasts while the input box is hidden.
    pub area: ratatui::layout::Rect,
}

/// Python `_active_batch`: hold children that were active in this batch listed
/// until the batch ends; brand-new ids join only while some child is active;
/// the selected child stays listed. Passes idle children through.
pub fn active_batch(
    known: &mut HashSet<String>,
    batch: &mut HashSet<String>,
    sessions: &[PublicChildSession],
    selected_session_id: Option<&str>,
) -> Vec<PublicChildSession> {
    let current: HashSet<String> = sessions.iter().map(|session| session.id.clone()).collect();
    let new_ids: HashSet<String> = current.difference(known).cloned().collect();
    *known = current.clone();
    *batch = batch.intersection(&current).cloned().collect();
    let active_ids: HashSet<String> = sessions
        .iter()
        .filter(|session| is_active(session.status))
        .map(|session| session.id.clone())
        .collect();
    if !active_ids.is_empty() {
        batch.extend(active_ids);
        batch.extend(new_ids);
    } else if selected_session_id.is_none_or(|id| !batch.contains(id)) {
        batch.clear();
    }
    sessions
        .iter()
        .filter(|session| {
            batch.contains(&session.id)
                || session.status == SessionStatus::Idle
                // The viewed child stays listed whatever its status, so the
                // read-only notice survives the child being killed. Python
                // only keeps that guarantee while the child sits in the
                // batch; pinning the selection here is a deliberate
                // hardening of that behavior.
                || selected_session_id == Some(session.id.as_str())
        })
        .cloned()
        .collect()
}

/// Python `SubagentList.update_sessions`: batch-filter the rows, reconcile the
/// highlight, and hand focus back when the list empties while focused.
pub(super) fn update_sessions(app: &mut App) {
    let subagents = &mut app.subagents;
    let previous_selected = subagents.list.selected_session_id.clone();
    // Python captures the highlighted id before rebuilding the options, so it
    // resolves against the previous rows and only then re-checks membership.
    let previous_highlighted = highlighted_id(&subagents.list, &subagents.list.rows);
    let rows = subagents.list_rows();
    let list = &mut subagents.list;
    let current_ids: HashSet<&str> = rows.iter().map(|row| row.id.as_str()).collect();
    if rows.is_empty() {
        let was_focused = list.focused;
        list.highlighted = 0;
        list.mouse_session_id = None;
        // Python `clear_options` resets `scroll_y` with the emptied list.
        list.free_scroll = None;
        list.rows = rows;
        if was_focused {
            focus_input(app);
        }
        return;
    }
    if let Some(mouse) = &list.mouse_session_id {
        if mouse != MAIN_SESSION_ID && !current_ids.contains(mouse.as_str()) {
            list.mouse_session_id = None;
        }
    }
    // Python keeps the highlight when the selection did not change and the
    // highlighted row still exists, else jumps to the selected row (or Main).
    let selected_id = list
        .selected_session_id
        .clone()
        .unwrap_or_else(|| MAIN_SESSION_ID.to_owned());
    let preferred = if previous_selected == list.selected_session_id {
        previous_highlighted
            .filter(|id| *id == MAIN_SESSION_ID || current_ids.contains(id.as_str()))
            .unwrap_or(selected_id)
    } else {
        selected_id
    };
    let previous_highlighted_row = list.highlighted;
    // Python rebuilds the options (`clear_options` + `add_options`, which
    // resets `scroll_y`) whenever the id set changed; only a same-id batch
    // update keeps the wheel-scrolled viewport.
    let rows_changed = {
        let previous_ids: Vec<&str> = list.rows.iter().map(|row| row.id.as_str()).collect();
        let current_ids: Vec<&str> = rows.iter().map(|row| row.id.as_str()).collect();
        previous_ids != current_ids
    };
    list.highlighted = if preferred == MAIN_SESSION_ID {
        0
    } else {
        rows.iter()
            .position(|row| row.id == preferred)
            .map(|position| position + 1)
            .unwrap_or(0)
    };
    // Python `scroll_to_highlight`: the wheel-scrolled viewport follows the
    // highlight again once the highlight itself moves.
    if rows_changed || list.highlighted != previous_highlighted_row {
        list.free_scroll = None;
    }
    list.rows = rows;
}

/// Python `_refresh_subagent_list`: the event-time list refresh.
pub fn refresh(app: &mut App) {
    if !app.subagents.status_list_enabled {
        if app.subagents.viewed_subagent_id.is_some() {
            show_main_chat(app, true);
            return;
        }
        update_sessions(app);
        return;
    }
    if let Some(viewed) = app.subagents.viewed_subagent_id.clone() {
        if !app
            .subagents
            .sessions
            .iter()
            .any(|session| session.id == viewed)
        {
            show_main_chat(app, true);
            return;
        }
    }
    update_sessions(app);
}

/// Python `App._show_main_chat`: leave the child view and restore the composer.
pub fn show_main_chat(app: &mut App, focus_input_requested: bool) {
    if app.subagents.viewed_subagent_id.is_some() {
        app.quit.cancel_confirmation();
        remember_scroll(app);
    }
    app.subagents.viewed_subagent_id = None;
    app.subagents.transcripts.select(None);
    if focus_input_requested {
        focus_input(app);
    }
    super::refresh_context_progress(app);
    update_sessions(app);
    restore_scroll(app, None);
}

/// Python `App._show_subagent_chat`: open a child's read-only view.
pub fn show_subagent_chat(app: &mut App, session_id: &str) {
    if !app.subagents.status_list_enabled {
        return;
    }
    if app.subagents.viewed_subagent_id.as_deref() != Some(session_id) {
        remember_scroll(app);
    }
    app.subagents.viewed_subagent_id = Some(session_id.to_owned());
    // Python `set_subagent_view(True)`: hide the composer, focus the list.
    crate::completion_manager::dismiss(app);
    app.subagents.list.focused = true;
    app.set_app_focus(false);
    let cached = app.subagents.transcripts.select(Some(session_id));
    super::refresh_context_progress(app);
    update_sessions(app);
    if !cached {
        app.subagents.transcripts.prepare(session_id);
        app.subagents.transcripts.select(Some(session_id));
    }
    restore_scroll(app, Some(session_id));
    schedule_refresh(app);
}

/// Python `SubagentList.focus_first`: take focus on the Main row.
pub fn focus_first(app: &mut App) -> bool {
    if app.subagents.list.rows.is_empty() {
        return false;
    }
    app.subagents.list.mouse_session_id = None;
    app.subagents.list.highlighted = 0;
    app.subagents.list.focused = true;
    app.set_app_focus(false);
    true
}

/// Python `SubagentList.Selected` → `App.on_subagent_list_selected`.
pub fn select(app: &mut App, session_id: Option<String>) {
    if session_id == app.subagents.viewed_subagent_id {
        return;
    }
    match session_id {
        // Selecting Main from a child view acts like Esc: back to the main
        // conversation with the input focused (Python keeps the list focused).
        None => show_main_chat(app, true),
        Some(id) => {
            if !app
                .subagents
                .sessions
                .iter()
                .any(|session| session.id == id)
            {
                show_main_chat(app, true);
                return;
            }
            show_subagent_chat(app, &id);
        }
    }
}

/// Keys the list owns while focused (Python `NavigableOptionList` bindings);
/// returns false so the app-level ladder (Esc, priority keys) can run.
pub fn handle_list_key(app: &mut App, key: KeyEvent) -> bool {
    let down = match key.code {
        KeyCode::Up | KeyCode::Char('k') => false,
        KeyCode::Down | KeyCode::Char('j') => true,
        KeyCode::Home => {
            app.subagents.list.mouse_session_id = None;
            app.subagents.list.free_scroll = None;
            app.subagents.list.highlighted = 0;
            return true;
        }
        KeyCode::End => {
            app.subagents.list.mouse_session_id = None;
            app.subagents.list.free_scroll = None;
            app.subagents.list.highlighted = app.subagents.list.rows.len();
            return true;
        }
        // Python `action_page_up`/`action_page_down`: the highlight jumps by
        // about one visible page of options.
        KeyCode::PageUp | KeyCode::PageDown => {
            let total = app.subagents.list.rows.len() + 1;
            let page = MAX_ROWS.min(total);
            app.subagents.list.mouse_session_id = None;
            app.subagents.list.free_scroll = None;
            if key.code == KeyCode::PageDown {
                app.subagents.list.highlighted =
                    (app.subagents.list.highlighted + page).min(total - 1);
            } else {
                app.subagents.list.highlighted =
                    app.subagents.list.highlighted.saturating_sub(page);
            }
            return true;
        }
        KeyCode::Enter => {
            let highlighted = app.subagents.list.highlighted;
            let session_id = if highlighted == 0 {
                None
            } else {
                app.subagents
                    .list
                    .rows
                    .get(highlighted - 1)
                    .map(|row| row.id.clone())
            };
            select(app, session_id);
            return true;
        }
        _ => return false,
    };
    app.subagents.list.mouse_session_id = None;
    app.subagents.list.free_scroll = None;
    if down {
        let last = app.subagents.list.rows.len();
        if app.subagents.list.highlighted < last {
            app.subagents.list.highlighted += 1;
        }
    } else if app.subagents.list.highlighted == 0 {
        // Python posts `FocusInputRequested` only when nothing is viewed.
        if app.subagents.list.selected_session_id.is_none() {
            focus_input(app);
        }
    } else {
        app.subagents.list.highlighted -= 1;
    }
    true
}

/// Give the composer back focus (Python `FocusInputRequested` → `focus_input`).
pub fn focus_input(app: &mut App) {
    app.subagents.list.focused = false;
    app.set_app_focus(true);
}

/// The wheel scrolls the viewport without moving the highlight (Python
/// `OptionList` is a `ScrollView`; the highlight pins it again on its next move).
pub fn wheel(app: &mut App, down: bool, step: usize) {
    let total = app.subagents.list.rows.len() + 1;
    let current = app
        .subagents
        .list
        .free_scroll
        .unwrap_or(app.subagents.list.rendered_scroll);
    app.subagents.list.free_scroll = Some(if down {
        (current + step).min(total - 1)
    } else {
        current.saturating_sub(step)
    });
}

/// The id of the highlighted row: Main or the child at `index - 1`.
fn highlighted_id(list: &ListState, rows: &[PublicChildSession]) -> Option<String> {
    if list.highlighted == 0 {
        return Some(MAIN_SESSION_ID.to_owned());
    }
    rows.get(list.highlighted - 1).map(|row| row.id.clone())
}
