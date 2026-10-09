//! Edit the composer text in `$VISUAL` / `$EDITOR` (Python `ExternalEditor`).

use std::ffi::OsString;
use std::path::Path;
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::app::App;
use crate::edit_history::Snapshot;

const DEFAULT_EDITOR: &str = "nano";

/// Ctrl+G asks the event loop to open the editor on the visible composer.
pub fn request(app: &mut App, key: &KeyEvent) -> bool {
    let requested = key.modifiers == KeyModifiers::CONTROL && key.code == KeyCode::Char('g');
    if requested && !crate::input::composer_hidden(app) {
        app.external_editor_requested = true;
    }
    requested
}

/// `$VISUAL`, then `$EDITOR`, then `nano`; blank values count as unset.
pub fn get_editor(visual: Option<String>, editor: Option<String>) -> String {
    [visual, editor]
        .into_iter()
        .flatten()
        .find(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_EDITOR.to_owned())
}

/// The editor command split like a shell (Python `shlex.split`), then the file.
pub fn command(editor: &str, path: &Path) -> Vec<OsString> {
    let parts = shlex::split(editor).unwrap_or_else(|| vec![editor.to_owned()]);
    let mut argv: Vec<OsString> = parts.into_iter().map(OsString::from).collect();
    argv.push(path.as_os_str().to_owned());
    argv
}

/// The saved text with `\n` line ends, or `None` when only trailing whitespace changed.
pub fn edited_text(initial: &str, saved: &str) -> Option<String> {
    let saved = saved.strip_prefix('\u{feff}').unwrap_or(saved);
    let saved = saved.replace("\r\n", "\n").replace('\r', "\n");
    let saved = saved.trim_end();
    (saved != initial.trim_end()).then(|| saved.to_owned())
}

/// Replace the composer with the edited text as one undoable edit.
pub fn apply(app: &mut App, text: String) {
    app.chat_input.normalize_positions();
    let before = Snapshot::capture(&app.chat_input);
    crate::long_paste::dismiss(app);
    // Loading resets the edit history; keep it so Undo brings the draft back.
    let history = std::mem::take(&mut app.chat_input.edit_history);
    crate::long_paste::load_collapsed(app, text);
    app.chat_input.edit_history = history;
    crate::composer_paths::rewrite_image_paths(&mut app.chat_input);
    app.chat_input.record_edit(before, true, Instant::now());
    crate::input::reset_history_state(app);
    crate::completion_manager::input_changed(app);
}
