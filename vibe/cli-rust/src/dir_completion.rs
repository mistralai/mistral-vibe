//! Folder completion after `/cd `: subfolders read off the UI thread, stale reads dropped.

use std::path::{Path, PathBuf};

use crate::app::App;
use crate::completion_manager::CompletionEntry;
use crate::input_modes::InputMode;

const LIMIT: usize = 100;
const PREFIX: &str = "cd ";

/// The folder and name prefix a read listed, and the names it found.
#[derive(Default)]
pub struct Dirs {
    requested: Option<(PathBuf, String)>,
    shown: Option<(PathBuf, String)>,
    names: Vec<String>,
    latest: u64,
}

impl Dirs {
    /// Whether a folder read is shown or on its way.
    pub fn holds_a_read(&self) -> bool {
        self.requested.is_some() || self.shown.is_some()
    }
}

/// A folder read for request `id`.
pub struct Event {
    id: u64,
    key: (PathBuf, String),
    names: Vec<String>,
}

/// The typed path's byte range, when the composer holds `/cd <path>` up to the caret.
pub fn span(app: &App, caret: usize) -> Option<(usize, usize)> {
    let input = &app.chat_input.input;
    let path = input.get(..caret)?.strip_prefix(PREFIX)?;
    let slash = app.chat_input.mode == InputMode::Slash;
    (slash && !has_unescaped_whitespace(path)).then_some((PREFIX.len(), caret))
}

/// Whether `path` holds whitespace a shell would split on: `My\ Folder` is one word.
fn has_unescaped_whitespace(path: &str) -> bool {
    let mut chars = path.chars();
    while let Some(ch) = chars.next() {
        match ch {
            '\\' if chars.next().is_some_and(|next| next == ' ') => {}
            ch if ch.is_whitespace() => return true,
            _ => {}
        }
    }
    false
}

/// The entries for `input[start..end]`, asking for a read when the folder or prefix changed.
pub fn entries(app: &mut App, start: usize, end: usize) -> Vec<CompletionEntry> {
    let typed = app.chat_input.input[start..end].to_owned();
    let windows = cfg!(windows);
    // `/`, or `\` too on Windows, ends the folder part; `\ ` escapes a space.
    let separator = typed.char_indices().rev().find(|&(at, ch)| {
        std::path::is_separator(ch) && !(ch == '\\' && typed[at + 1..].starts_with(' '))
    });
    let (parent, prefix) = separator.map_or(("", typed.as_str()), |(at, ch)| {
        let end = at + ch.len_utf8();
        (&typed[..end], &typed[end..])
    });
    // Names are read and listed unescaped, then inserted escaped after the
    // folder part as typed.
    let prefix = crate::paste_path::unescape_on(prefix, windows);
    let cwd = PathBuf::from(app.session.cwd.clone().unwrap_or_default());
    let home = crate::utils::paths::user_home().unwrap_or_default();
    let Ok(dir) = crate::cwd::resolve(
        if parent.is_empty() { "." } else { parent },
        &cwd,
        &home,
        None,
    ) else {
        return Vec::new();
    };
    let key = (dir, prefix);
    let dirs = &mut app.completion.cd_dirs;
    if dirs.shown.as_ref() == Some(&key) {
        return dirs
            .names
            .iter()
            .map(|name| CompletionEntry {
                label: format!("{parent}{}/", crate::paste_path::escape_on(name, windows)),
                description: String::new(),
                origin: None,
            })
            .collect();
    }
    if dirs.requested.as_ref() != Some(&key) {
        request(app, key);
    }
    Vec::new()
}

fn request(app: &mut App, key: (PathBuf, String)) {
    let dirs = &mut app.completion.cd_dirs;
    dirs.latest += 1;
    dirs.requested = Some(key.clone());
    let id = dirs.latest;
    if app.events.is_none() {
        return;
    }
    crate::ui_event::spawn(app, async move {
        let read = key.clone();
        let names = crate::work::spawn_blocking(move || subfolders(&read.0, &read.1))
            .await
            .unwrap_or_default();
        Event { id, key, names }
    });
}

/// Up to `LIMIT` sorted subfolder names of `dir` starting with `prefix`; hidden ones only for a `.` prefix.
pub fn subfolders(dir: &Path, prefix: &str) -> Vec<String> {
    let Ok(read) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut names: Vec<String> = read
        .flatten()
        .filter_map(|entry| {
            entry
                .file_name()
                .into_string()
                .ok()
                .map(|name| (entry, name))
        })
        .filter(|(_, name)| {
            name.starts_with(prefix) && (prefix.starts_with('.') || !name.starts_with('.'))
        })
        .filter(|(entry, _)| entry.path().is_dir())
        .map(|(_, name)| name)
        .collect();
    names.sort();
    names.truncate(LIMIT);
    names
}

/// Drop the folder list shown, so the next `/cd` reads its folder again.
pub fn forget(app: &mut App) {
    let dirs = &mut app.completion.cd_dirs;
    dirs.requested = None;
    dirs.shown = None;
    dirs.names.clear();
}

pub fn apply_event(app: &mut App, event: Event) {
    let dirs = &mut app.completion.cd_dirs;
    if event.id != dirs.latest {
        return;
    }
    dirs.shown = Some(event.key);
    dirs.names = event.names;
    crate::completion_manager::refresh(app);
}

/// Whether `input[start..end]` already names an existing folder, as `/cd` resolves it.
pub fn names_a_folder(app: &App, start: usize, end: usize) -> bool {
    let cwd = PathBuf::from(app.session.cwd.clone().unwrap_or_default());
    let home = crate::utils::paths::user_home().unwrap_or_default();
    let previous = app.session.previous_cwd.as_deref().map(Path::new);
    crate::cwd::resolve(&app.chat_input.input[start..end], &cwd, &home, previous)
        .is_ok_and(|path| crate::cwd::existing_dir(&path).is_some())
}

/// Put the accepted `<dir>/` in place of the typed path, so typing goes on inside it.
pub fn insert(app: &mut App, start: usize, end: usize, label: &str) {
    app.chat_input.input.replace_range(start..end, label);
    app.chat_input.cursor = start + label.len();
}
