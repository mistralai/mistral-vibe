//! Status-list state and interaction.

use std::collections::HashSet;

use crossterm::event::{KeyCode, KeyEvent};

use crate::app::App;
use crate::server::PublicChildSession;
use crate::server::SessionStatus;
use crate::subagents::is_active;
use crate::subagents::view::{remember_scroll, restore_scroll, schedule_refresh};

/// Row 0, the main-conversation row.
pub const MAIN_SESSION_ID: &str = "\u{0}main";
/// Rendered row cap.
pub const MAX_ROWS: usize = 7;

/// List navigation state; the rendered rows derive from it.
#[derive(Default)]
pub struct ListState {
    /// Every id ever seen, for new-id detection.
    pub known_session_ids: HashSet<String>,
    /// Children held listed until the batch ends.
    pub batch_session_ids: HashSet<String>,
    /// The viewed child, or `None` for main.
    pub selected_session_id: Option<String>,
    /// Highlighted row (0 is the Main conversation row) and the viewport
    /// (Python `ScrollView` wheel scrolling vs `scroll_to_highlight`).
    pub options: crate::option_list::OptionList<crate::option_list::SingleAction>,
    /// Python `_mouse_session_id`: the row under the mouse, if any.
    pub mouse_session_id: Option<String>,
    /// Whether the list owns keyboard focus.
    pub focused: bool,
    /// Rows the list renders, recomputed on every state change (Python options).
    pub rows: Vec<PublicChildSession>,
    /// Painted widget area, used to anchor toasts while the input box is hidden.
    pub area: ratatui::layout::Rect,
}

/// List every child but stopped ones; a stopped child stays while its batch runs or while viewed.
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
                // Only stopped children leave; failed and unknown ones stay listed.
                || session.status != SessionStatus::Archived
                // The viewed child stays listed whatever its status, so the
                // read-only notice survives the child being killed, even once
                // it left the batch.
                || selected_session_id == Some(session.id.as_str())
        })
        .cloned()
        .collect()
}

/// Batch-filter the rows, reconcile the
/// highlight, and hand focus back when the list empties while focused.
pub(super) fn update_sessions(app: &mut App) {
    let subagents = &mut app.subagents;
    let previous_selected = subagents.list.selected_session_id.clone();
    // Capture the highlighted id before rebuilding the rows, so it resolves
    // against the previous rows and only then re-checks membership.
    let previous_highlighted = highlighted_id(&subagents.list, &subagents.list.rows);
    let rows = subagents.list_rows();
    let list = &mut subagents.list;
    let current_ids: HashSet<&str> = rows.iter().map(|row| row.id.as_str()).collect();
    if rows.is_empty() {
        let was_focused = list.focused;
        list.mouse_session_id = None;
        // Python `clear_options` resets `scroll_y` with the emptied list.
        list.options.reset();
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
    // Keep the highlight when the selection did not change and the highlighted
    // row still exists, else jump to the selected row (or Main).
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
    let previous_highlighted_row = list.options.selected();
    // Python rebuilds the options (`clear_options` + `add_options`, which
    // resets `scroll_y`) whenever the id set changed; only a same-id batch
    // update keeps the wheel-scrolled viewport.
    let rows_changed = {
        let previous_ids: Vec<&str> = list.rows.iter().map(|row| row.id.as_str()).collect();
        let current_ids: Vec<&str> = rows.iter().map(|row| row.id.as_str()).collect();
        previous_ids != current_ids
    };
    list.options.highlight(if preferred == MAIN_SESSION_ID {
        0
    } else {
        rows.iter()
            .position(|row| row.id == preferred)
            .map(|position| position + 1)
            .unwrap_or(0)
    });
    // Python `scroll_to_highlight`: the wheel-scrolled viewport follows the
    // highlight again once the highlight itself moves.
    if rows_changed {
        list.options.reset_viewport();
    } else if list.options.selected() != previous_highlighted_row {
        list.options.follow();
    }
    list.rows = rows;
}

/// The event-time list refresh.
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

/// Leave the child view and restore the composer.
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

/// Open a child's read-only view.
pub fn show_subagent_chat(app: &mut App, session_id: &str) {
    if !app.subagents.status_list_enabled {
        return;
    }
    if app.subagents.viewed_subagent_id.as_deref() != Some(session_id) {
        remember_scroll(app);
    }
    app.subagents.viewed_subagent_id = Some(session_id.to_owned());
    // Hide the composer, focus the list.
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

/// Take focus on the Main row.
pub fn focus_first(app: &mut App) -> bool {
    if app.subagents.list.rows.is_empty() {
        return false;
    }
    app.subagents.list.mouse_session_id = None;
    app.subagents.list.options.highlight(0);
    app.subagents.list.focused = true;
    app.set_app_focus(false);
    crate::selection::blur_composer(app);
    true
}

/// A list row was selected: view that child, or go back to Main.
pub fn select(app: &mut App, session_id: Option<String>) {
    if session_id == app.subagents.viewed_subagent_id {
        return;
    }
    match session_id {
        // Selecting Main from a child view acts like Esc: back to the main
        // conversation with the input focused.
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

/// Keys the list owns while focused;
/// returns false so the app-level ladder (Esc, priority keys) can run.
pub fn handle_list_key(app: &mut App, key: KeyEvent) -> bool {
    let down = match key.code {
        KeyCode::Up | KeyCode::Char('k') => false,
        KeyCode::Down | KeyCode::Char('j') => true,
        KeyCode::Home => {
            app.subagents.list.mouse_session_id = None;
            app.subagents.list.options.select(0);
            return true;
        }
        KeyCode::End => {
            app.subagents.list.mouse_session_id = None;
            let last = app.subagents.list.rows.len();
            app.subagents.list.options.select(last);
            return true;
        }
        // Page up/down: the highlight jumps by
        // about one visible page of options.
        KeyCode::PageUp | KeyCode::PageDown => {
            let total = app.subagents.list.rows.len() + 1;
            let page = MAX_ROWS.min(total);
            app.subagents.list.mouse_session_id = None;
            let options = &mut app.subagents.list.options;
            options.select(if key.code == KeyCode::PageDown {
                (options.selected() + page).min(total - 1)
            } else {
                options.selected().saturating_sub(page)
            });
            return true;
        }
        KeyCode::Enter => {
            let session_id = session_id_at(app, app.subagents.list.options.selected());
            select(app, session_id);
            return true;
        }
        _ => return false,
    };
    app.subagents.list.mouse_session_id = None;
    let total = app.subagents.list.rows.len() + 1;
    let moved = app
        .subagents
        .list
        .options
        .step(down, total, false, |_| true);
    // Python posts `FocusInputRequested` only when nothing is viewed.
    if !down && !moved && app.subagents.list.selected_session_id.is_none() {
        focus_input(app);
    }
    true
}

/// Give the composer back focus.
pub fn focus_input(app: &mut App) {
    app.subagents.list.focused = false;
    app.set_app_focus(true);
}

/// The session a row stands for: `None` for Main, else the child at `index - 1`.
pub fn session_id_at(app: &App, index: usize) -> Option<String> {
    let index = index.checked_sub(1)?;
    app.subagents.list.rows.get(index).map(|row| row.id.clone())
}

/// The id of the highlighted row: Main or the child at `index - 1`.
fn highlighted_id(list: &ListState, rows: &[PublicChildSession]) -> Option<String> {
    if list.options.selected() == 0 {
        return Some(MAIN_SESSION_ID.to_owned());
    }
    rows.get(list.options.selected() - 1)
        .map(|row| row.id.clone())
}
