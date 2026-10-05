//! Shared slash-command, file-path, and skill-mention completion state.

use tokio::sync::watch;

use crate::app::App;
use crate::commands;
use crate::input_modes::InputMode;
use crate::utils::fuzzy;

const FILE_MATCH_LIMIT: usize = 100;

#[derive(Clone, PartialEq)]
pub struct CompletionEntry {
    pub label: String,
    pub description: String,
}

enum ActiveCompletion {
    Slash { start: usize },
    File { start: usize, end: usize },
    Skill { start: usize, end: usize },
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
    app.chat_input.sync_mentions();
    if app.completion.dismissed {
        app.completion.entries.clear();
        return;
    }
    let entries = match active(app) {
        Some(ActiveCompletion::Slash { start }) => slash_entries(app, start),
        Some(ActiveCompletion::File { start, end }) => file_entries(app, start, end),
        Some(ActiveCompletion::Skill { start, end }) => skill_entries(app, start, end),
        None => Vec::new(),
    };
    // Python keeps the highlighted item only across re-renders that leave the
    // list identical (path_completion `_update_suggestions`); a caret move that
    // lands on another token rebuilds a different list and restarts at the top.
    if entries != app.completion.entries {
        app.completion.selected = 0;
        app.completion.scroll = 0;
        app.completion.reveal = false;
    }
    app.completion.entries = entries;
}

/// Apply a file-index update not yet seen, so a frame never pairs a ready index with a stale `@` popup.
pub fn sync_files(app: &mut App, files: &mut watch::Receiver<u64>) {
    if files.has_changed().unwrap_or(false) {
        files.borrow_and_update();
        refresh(app);
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
    let mut replacement = entry.label.clone();
    app.chat_input.sync_mentions();
    match active(app) {
        Some(ActiveCompletion::Slash { start }) => {
            app.chat_input.input.replace_range(
                start..,
                replacement.strip_prefix('/').unwrap_or(&replacement),
            );
            app.chat_input.cursor = app.chat_input.input.len();
        }
        Some(ActiveCompletion::File { start, end } | ActiveCompletion::Skill { start, end }) => {
            let mention_end = start + replacement.len();
            if !replacement.ends_with('/') {
                replacement.push(' ');
            }
            app.chat_input.input.replace_range(start..end, &replacement);
            app.chat_input.cursor = start + replacement.len();
            app.chat_input.sync_mentions_at(start);
            app.chat_input.add_mention(start, mention_end);
        }
        None => return false,
    }
    app.chat_input.anchor = None;
    app.completion.dismissed = true;
    app.completion.entries.clear();
    app.completion.selected = 0;
    app.completion.scroll = 0;
    true
}

/// Tab accepts like `accept`, then leaves a space after a slash command so its arguments (and hint) follow.
pub fn tab(app: &mut App) {
    let slash = matches!(active(app), Some(ActiveCompletion::Slash { .. }));
    if accept(app) && slash {
        app.chat_input.input.push(' ');
        app.chat_input.cursor = app.chat_input.input.len();
    }
}

/// True when the active completion is a file (`@`) completion.
pub fn active_is_file(app: &App) -> bool {
    matches!(active(app), Some(ActiveCompletion::File { .. }))
}

/// True when the active completion is an inline `@file` or `/skill` mention,
/// which accepts on Enter without submitting, unlike a slash completion which
/// runs the command.
pub fn active_is_mention(app: &App) -> bool {
    matches!(
        active(app),
        Some(ActiveCompletion::File { .. } | ActiveCompletion::Skill { .. })
    )
}

fn active(app: &App) -> Option<ActiveCompletion> {
    let input = &app.chat_input.input;
    let caret = crate::chat_input::clamp_offset(input, app.chat_input.cursor);
    // An accepted mention is settled: the caret after it completes nothing.
    if app
        .chat_input
        .mentions
        .spans(input)
        .iter()
        .any(|mention| mention.end == caret)
    {
        return None;
    }
    if let Some(start) = skill_mention_start(app) {
        return Some(ActiveCompletion::Skill { start, end: caret });
    }
    let slash_start = match app.chat_input.mode {
        InputMode::Slash => Some(0),
        InputMode::Prompt if input.starts_with('/') => Some(1),
        _ => None,
    };
    if let Some(start) = slash_start {
        // Anything typed past the command word, even a space, closes the menu.
        return (!input.contains(char::is_whitespace)).then_some(ActiveCompletion::Slash { start });
    }
    let start = input[..caret].rfind('@')?;
    let query = &input[start + 1..caret];
    if query.contains(' ') {
        return None;
    }
    Some(ActiveCompletion::File { start, end: caret })
}

/// Start of a `/skill` token ending at the caret: the whitespace-delimited
/// word must begin with `/` past the start of the input, like the app
/// server's mention parsing. Shell mode keeps paths literal, and the command
/// word a leading `/` opens stays a slash command.
fn skill_mention_start(app: &App) -> Option<usize> {
    let input = &app.chat_input.input;
    let end = crate::chat_input::clamp_offset(input, app.chat_input.cursor);
    let start = input[..end]
        .char_indices()
        .rev()
        .find(|(_, character)| character.is_whitespace())
        .map_or(0, |(index, character)| index + character.len_utf8());
    let command_word = start == 0 && app.chat_input.mode == InputMode::Slash;
    let mentionable = matches!(app.chat_input.mode, InputMode::Prompt | InputMode::Slash);
    let mention = start > 0 && input[start..end].starts_with('/');
    (mentionable && !command_word && mention).then_some(start)
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
fn slash_entries(app: &App, start: usize) -> Vec<CompletionEntry> {
    // Python completes on the word up to the caret, not the whole word.
    let input = &app.chat_input.input;
    let end = crate::chat_input::clamp_offset(input, app.chat_input.cursor).max(start);
    let query = input[start..end].to_lowercase();
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

fn file_entries(app: &App, start: usize, end: usize) -> Vec<CompletionEntry> {
    let query = app.chat_input.input[start + 1..end].replace('\\', "/");
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

/// User-invocable skills matching the `/` query, best score first; ties keep
/// the discovery order.
fn skill_entries(app: &App, start: usize, end: usize) -> Vec<CompletionEntry> {
    let query = app.chat_input.input[start + 1..end].to_lowercase();
    let mut scored: Vec<(i64, CompletionEntry)> = app
        .completion
        .skills
        .iter()
        .filter_map(|(name, description)| {
            let score = fuzzy::score(&query, &name.to_lowercase())?;
            Some((
                score,
                CompletionEntry {
                    label: format!("/{name}"),
                    description: description.clone(),
                },
            ))
        })
        .collect();
    scored.sort_by_key(|(score, _)| std::cmp::Reverse(*score));
    scored.into_iter().map(|(_, entry)| entry).collect()
}
