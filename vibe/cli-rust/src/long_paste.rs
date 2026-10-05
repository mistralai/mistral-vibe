//! Long pastes collapse into an atomic `[Pasted n characters]` placeholder.

use std::sync::Arc;

use crate::app::App;

/// A paste longer than this many characters collapses.
pub const MAX_INLINE_CHARS: usize = 1000;
/// A paste with more lines than this collapses, whatever its length.
pub const MAX_INLINE_LINES: usize = 10;
/// Shown in the inline notice slot when a paste collapses.
pub const HINT: &str = "Paste the same text again to show it in full";
const HINT_SECS: u64 = 4;

pub fn is_long(text: &str) -> bool {
    text.chars().count() > MAX_INLINE_CHARS || text.lines().count() > MAX_INLINE_LINES
}

pub fn placeholder(text: &str) -> String {
    crate::collapsed_pastes::label(text.chars().count())
}

/// Load `text` into the input with every paste it holds that the composer
/// still remembers collapsed again, so a draft or recalled prompt keeps its
/// placeholders.
pub fn load_collapsed(app: &mut App, text: String) {
    let display = app.chat_input.collapsed_pastes.display(&text);
    app.chat_input.load_full_text(text);
    restore(app, display.as_ref());
    crate::image_placeholders::mark_known(&mut app.chat_input);
}

/// Collapse again, in a prompt just loaded into the input, each paste its sent
/// message showed collapsed, so rewinding keeps the placeholders.
pub fn restore(app: &mut App, display: Option<&serde_json::Value>) {
    let input = &mut app.chat_input;
    let offset = input.mode.prefix().map_or(0, char::len_utf8);
    let ranges = crate::collapsed_pastes::ranges(&input.full_text(), display);
    for (start, end, chars) in ranges.into_iter().rev() {
        let (Some(start), Some(end)) = (start.checked_sub(offset), end.checked_sub(offset)) else {
            continue;
        };
        let paste: Arc<str> = Arc::from(&input.input[start..end]);
        let label = crate::collapsed_pastes::label(chars);
        input.input.replace_range(start..end, &label);
        input.sync_mentions_at(start);
        input.collapsed_pastes.remember(paste.clone());
        input
            .mentions
            .add(&input.input, start, start + label.len(), Some(paste));
    }
}

/// Show in full the paste just collapsed when `text` pastes it again before
/// any other edit; true when it did.
pub fn expand(app: &mut App, text: &str) -> bool {
    app.chat_input.sync_mentions();
    let expandable = app.chat_input.expandable_paste.take();
    let span = expandable
        .filter(|paste| **paste == *text)
        .and_then(|paste| app.chat_input.mentions.span_of(&paste));
    let Some((start, end)) = span else {
        dismiss(app);
        return false;
    };
    app.chat_input.input.replace_range(start..end, text);
    app.chat_input.cursor = start + text.len();
    app.chat_input.anchor = None;
    app.chat_input.sync_mentions_at(start);
    if !app.chat_input.mentions.holds_paste(text) {
        app.chat_input.collapsed_pastes.forget(text);
    }
    dismiss(app);
    true
}

/// End the paste-again offer: an edit makes the next paste a paste of its own.
pub fn dismiss(app: &mut App) {
    app.chat_input.expandable_paste = None;
    if app
        .overlays
        .notice
        .as_ref()
        .is_some_and(|notice| notice.text == HINT)
    {
        app.overlays.notice = None;
    }
}

/// Insert `text` collapsed at the caret, as a placeholder mention standing for it.
pub fn insert_collapsed(app: &mut App, text: &str) {
    let label = placeholder(text);
    let input = &mut app.chat_input;
    let start = input.cursor;
    crate::utils::input_edit::insert(&mut input.input, &mut input.cursor, &label);
    input.sync_mentions_at(start);
    let paste: Arc<str> = Arc::from(text);
    input.collapsed_pastes.remember(paste.clone());
    input.expandable_paste = Some(paste.clone());
    input
        .mentions
        .add(&input.input, start, start + label.len(), Some(paste));
    crate::ui::notice::show(app, HINT, HINT_SECS);
}
