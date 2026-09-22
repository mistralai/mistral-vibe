//! Shared slash-command and file-path completion state.

use crate::app::App;
use crate::commands;
use crate::input_modes::InputMode;
use crate::utils::fuzzy;

const FILE_MATCH_LIMIT: usize = 100;

#[derive(Clone)]
pub struct CompletionEntry {
    pub label: String,
    pub description: String,
}

enum ActiveCompletion {
    Slash { start: usize, end: usize },
    File { start: usize },
}

pub fn input_changed(app: &mut App) {
    app.completion.dismissed = false;
    app.completion.selected = 0;
    app.completion.scroll = 0;
    app.completion.reveal = false;
    refresh(app);
}

/// Close the popup after history recall (Python `_navigating_history`): a recalled slash/`@` entry must not reopen the menu, so the next Up/Down keeps recalling; it reopens on the next real edit.
pub fn reset_for_recall(app: &mut App) {
    app.completion.dismissed = true;
    app.completion.entries.clear();
    app.completion.selected = 0;
    app.completion.scroll = 0;
    app.completion.reveal = false;
}

pub fn refresh(app: &mut App) {
    if app.completion.dismissed {
        app.completion.entries.clear();
        return;
    }
    app.completion.entries = match active(app) {
        Some(ActiveCompletion::Slash { .. }) => slash_entries(app),
        Some(ActiveCompletion::File { start }) => file_entries(app, start),
        None => Vec::new(),
    };
    if app.completion.selected >= app.completion.entries.len() {
        app.completion.selected = 0;
    }
}

pub fn is_open(app: &App) -> bool {
    !app.completion.entries.is_empty()
}

pub fn navigate(app: &mut App, down: bool) {
    let count = app.completion.entries.len();
    if count == 0 {
        return;
    }
    app.completion.selected = if down {
        (app.completion.selected + 1) % count
    } else {
        (app.completion.selected + count - 1) % count
    };
    app.completion.reveal = true;
}

pub fn wheel(app: &mut App, down: bool, delta: usize) {
    app.completion.scroll = if down {
        app.completion.scroll.saturating_add(delta)
    } else {
        app.completion.scroll.saturating_sub(delta)
    };
}

pub fn dismiss(app: &mut App) -> bool {
    if !is_open(app) {
        return false;
    }
    app.completion.dismissed = true;
    app.completion.entries.clear();
    true
}

pub fn accept(app: &mut App) -> bool {
    let Some(entry) = app.completion.entries.get(app.completion.selected) else {
        return false;
    };
    let replacement = entry.label.clone();
    match active(app) {
        Some(ActiveCompletion::Slash { start, end }) => app.chat_input.input.replace_range(
            start..end,
            replacement.strip_prefix('/').unwrap_or(&replacement),
        ),
        Some(ActiveCompletion::File { start }) => app
            .chat_input
            .input
            .replace_range(start.., &format!("{replacement} ")),
        None => return false,
    }
    app.completion.dismissed = true;
    app.completion.entries.clear();
    app.completion.selected = 0;
    app.completion.scroll = 0;
    true
}

/// True when the active completion is a file (`@`) completion, which accepts on
/// Enter without submitting, unlike a slash completion which runs the command.
pub fn active_is_file(app: &App) -> bool {
    matches!(active(app), Some(ActiveCompletion::File { .. }))
}

fn active(app: &App) -> Option<ActiveCompletion> {
    let input = &app.chat_input.input;
    let slash_start = match app.chat_input.mode {
        InputMode::Slash => Some(0),
        InputMode::Prompt if input.starts_with('/') => Some(1),
        _ => None,
    };
    if let Some(start) = slash_start {
        let end = input.find(char::is_whitespace).unwrap_or(input.len());
        return Some(ActiveCompletion::Slash { start, end });
    }
    let start = input.rfind('@')?;
    let query = &input[start + 1..];
    if query.contains(' ') {
        return None;
    }
    Some(ActiveCompletion::File { start })
}

/// `/help` and `/config` outrank equal matches (Python `_PROMOTED_BOOSTS`,
/// scaled like `fuzzy::score`, which is 20x the Python score).
fn boost(label: &str) -> i64 {
    match label {
        "/help" => 40,
        "/config" => 20,
        _ => 0,
    }
}

/// Matching commands, best score first; ties keep the alphabetical entry order
/// (Python `CommandCompleter._fuzzy_filter`, a stable sort on the score).
fn slash_entries(app: &App) -> Vec<CompletionEntry> {
    // Python completes on the word up to the caret, not the whole word.
    let word_end = app
        .chat_input
        .input
        .find(char::is_whitespace)
        .unwrap_or(app.chat_input.input.len());
    let start = usize::from(
        app.chat_input.mode == InputMode::Prompt && app.chat_input.input.starts_with('/'),
    );
    let end = word_end.min(app.chat_input.cursor).max(start);
    let query = app.chat_input.input[start..end].to_lowercase();
    let mut scored: Vec<(i64, CompletionEntry)> = commands::entries(&app.completion.skills)
        .into_iter()
        .filter_map(|(label, description)| {
            let score = fuzzy::score(&query, &label[1..].to_lowercase())? + boost(&label);
            Some((score, CompletionEntry { label, description }))
        })
        .collect();
    scored.sort_by_key(|(score, _)| std::cmp::Reverse(*score));
    scored.into_iter().map(|(_, entry)| entry).collect()
}

fn file_entries(app: &App, start: usize) -> Vec<CompletionEntry> {
    let query = app.chat_input.input[start + 1..].replace('\\', "/");
    app.completion
        .files
        .matching(&query, FILE_MATCH_LIMIT)
        .into_iter()
        .map(|label| CompletionEntry {
            label,
            description: String::new(),
        })
        .collect()
}
