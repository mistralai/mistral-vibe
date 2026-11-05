//! Ctrl+R reverse history search: the query is typed in the bottom bar while the composer previews the match.

use std::collections::HashSet;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::{App, Severity};
use crate::completion_manager;
use crate::edit_history::{EditHistory, Snapshot};
use crate::ui::notice;

pub const VOICE_MOVED: &str = "Voice recording moved to Ctrl+T";
const NOTICE_SECS: u64 = 4;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    /// Empty query: the composer shows the draft.
    Idle,
    /// The composer previews the selected match.
    Match,
    /// Nothing matches: the composer shows the draft and the search stays open.
    NoMatch,
}

pub struct ReverseSearch {
    pub query: String,
    pub status: Status,
    /// The composer as Ctrl+R found it, given back on cancel.
    draft: Snapshot,
    /// The draft's undo history, set aside while the composer shows previews.
    edit_history: EditHistory,
    /// Distinct matches, newest first.
    matches: Vec<String>,
    selected: usize,
}

/// Distinct entries containing `query`, ignoring case, newest first.
pub fn matching(history: &[String], query: &str) -> Vec<String> {
    let query = query.to_lowercase();
    let mut seen = HashSet::new();
    history
        .iter()
        .rev()
        .filter(|entry| entry.to_lowercase().contains(&query))
        .filter(|entry| seen.insert(entry.as_str()))
        .cloned()
        .collect()
}

/// Byte ranges of `text` matching `query`, ignoring case.
pub fn match_ranges(text: &str, query: &str) -> Vec<(usize, usize)> {
    let query = query.to_lowercase();
    if query.is_empty() {
        return Vec::new();
    }
    let mut folded = String::with_capacity(text.len());
    let mut origin = Vec::with_capacity(text.len());
    for (start, character) in text.char_indices() {
        for lower in character.to_lowercase() {
            folded.push(lower);
            origin.resize(folded.len(), (start, start + character.len_utf8()));
        }
    }
    folded
        .match_indices(&query)
        .map(|(at, found)| (origin[at].0, origin[at + found.len() - 1].1))
        .collect()
}

/// Ctrl+R: open an empty search over the draft; voice users are told recording moved.
pub fn open(app: &mut App) {
    if app.voice.mode_enabled {
        notice::show(app, Severity::Information, VOICE_MOVED, NOTICE_SECS);
    }
    crate::long_paste::dismiss(app);
    completion_manager::dismiss(app);
    app.chat_input.normalize_positions();
    // Searching from a recalled entry gives back the typed draft, as Down would.
    let draft = app
        .chat_input
        .recall_draft
        .take()
        .unwrap_or_else(|| Snapshot::capture(&app.chat_input));
    crate::input::reset_history_state(app);
    draft.clone().restore(&mut app.chat_input);
    app.reverse_search = Some(ReverseSearch {
        query: String::new(),
        status: Status::Idle,
        draft,
        edit_history: std::mem::take(&mut app.chat_input.edit_history),
        matches: Vec::new(),
        selected: 0,
    });
}

/// Route a key while searching; false lets app-level bindings run.
pub fn handle_key(app: &mut App, key: &KeyEvent) -> bool {
    if app.reverse_search.is_none() || crate::input::composer_hidden(app) {
        return false;
    }
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    let typed = !key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    match key.code {
        KeyCode::Up => step(app, true),
        KeyCode::Char('r') if ctrl => step(app, true),
        KeyCode::Down => step(app, false),
        KeyCode::Char('s') if ctrl => step(app, false),
        KeyCode::Esc => _ = cancel(app),
        KeyCode::Enter => accept(app),
        KeyCode::Backspace => edit_query(app, |query| _ = query.pop()),
        KeyCode::Char('h') if ctrl => edit_query(app, |query| _ = query.pop()),
        KeyCode::Char('u') if ctrl => edit_query(app, String::clear),
        KeyCode::Char('o') if ctrl => return false,
        // Voice users follow the Ctrl+T hint: the draft comes back and recording starts.
        KeyCode::Char('t') if ctrl && app.voice.mode_enabled => {
            _ = cancel(app);
            return false;
        }
        KeyCode::Char(character) if typed => edit_query(app, |query| query.push(character)),
        _ => {}
    }
    true
}

/// Append a paste to the query; false when not searching.
pub fn paste(app: &mut App, pasted: &str) -> bool {
    if app.reverse_search.is_none() {
        return false;
    }
    let pasted: String = pasted
        .replace("\r\n", "\n")
        .replace('\r', "\n")
        .chars()
        .filter(|character| !character.is_control() || matches!(character, '\n' | '\t'))
        .collect();
    edit_query(app, |query| query.push_str(&pasted));
    true
}

/// Leave the search and give the draft back; false when not searching.
pub fn cancel(app: &mut App) -> bool {
    let Some(search) = app.reverse_search.take() else {
        return false;
    };
    search.draft.restore(&mut app.chat_input);
    app.chat_input.edit_history = search.edit_history;
    completion_manager::input_changed(app);
    true
}

/// Byte ranges of the composer text that match the query, while a match is previewed.
pub fn highlights(app: &App) -> Vec<(usize, usize)> {
    let Some(search) = app
        .reverse_search
        .as_ref()
        .filter(|search| search.status == Status::Match)
    else {
        return Vec::new();
    };
    let prefix = app.chat_input.mode.prefix().map_or(0, char::len_utf8);
    match_ranges(&app.chat_input.full_text(), &search.query)
        .into_iter()
        .map(|(lo, hi)| (lo.saturating_sub(prefix), hi.saturating_sub(prefix)))
        .filter(|(lo, hi)| lo < hi)
        .collect()
}

/// A query edit restarts the search from the newest entry.
fn edit_query(app: &mut App, edit: impl FnOnce(&mut String)) {
    let Some(search) = app.reverse_search.as_mut() else {
        return;
    };
    edit(&mut search.query);
    search.selected = 0;
    search.matches = match search.query.is_empty() {
        true => Vec::new(),
        false => matching(app.chat_input.history.entries(), &search.query),
    };
    show(app);
}

/// Move to an older or newer match, staying on the last one at either end.
fn step(app: &mut App, older: bool) {
    let Some(search) = app.reverse_search.as_mut() else {
        return;
    };
    if search.status != Status::Match {
        return;
    }
    let next = match older {
        true => (search.selected + 1).min(search.matches.len() - 1),
        false => search.selected.saturating_sub(1),
    };
    if next != search.selected {
        search.selected = next;
        show(app);
    }
}

/// Preview the selected match, or give the draft back while there is none.
fn show(app: &mut App) {
    let Some(search) = app.reverse_search.as_mut() else {
        return;
    };
    let preview = search.matches.get(search.selected).cloned();
    search.status = match (search.query.is_empty(), &preview) {
        (true, _) => Status::Idle,
        (false, Some(_)) => Status::Match,
        (false, None) => Status::NoMatch,
    };
    match preview {
        Some(text) => {
            crate::long_paste::load_collapsed(app, text);
            crate::composer_paths::rewrite_image_paths(&mut app.chat_input);
        }
        None => search.draft.clone().restore(&mut app.chat_input),
    }
    completion_manager::reset_for_recall(app);
}

/// Enter keeps the previewed match as an editable draft; Undo brings the old draft back.
fn accept(app: &mut App) {
    if app
        .reverse_search
        .as_ref()
        .is_none_or(|search| search.status != Status::Match)
    {
        return;
    }
    let Some(search) = app.reverse_search.take() else {
        return;
    };
    app.chat_input.cursor = app.chat_input.input.len();
    app.chat_input.anchor = None;
    app.chat_input.edit_history = search.edit_history;
    app.chat_input
        .record_edit(search.draft, true, crate::clock::now());
    crate::input::reset_history_state(app);
    completion_manager::reset_for_recall(app);
}
