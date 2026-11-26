//! Long pastes collapse into `[Pasted n characters]`; pasting again undoes that or a path paste.

use std::sync::Arc;

use crate::app::{App, ChatInput, Severity};

/// A paste longer than this many characters collapses.
pub const MAX_INLINE_CHARS: usize = 1000;
/// A paste with more lines than this collapses, whatever its length.
pub const MAX_INLINE_LINES: usize = 10;
/// Shown in the inline notice slot when a paste collapses.
pub const HINT: &str = "Paste the same text again to show it in full";
/// Shown in the inline notice slot when pasted paths become placeholders or mentions.
pub const LITERAL_HINT: &str = "Paste the same path again to keep it as text";
const HINT_SECS: u64 = 4;

/// What pasting the same text again undoes, until the next edit.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Offer {
    /// A long paste collapsed into its placeholder.
    Collapsed(Arc<str>),
    /// Pasted paths turned into the `tokens` inserted at byte `start`, boundary spaces included.
    Paths {
        paste: String,
        start: usize,
        tokens: String,
    },
    /// A path list pasted at byte `start`, awaiting its probe: pasting again adds a copy.
    Probing { paste: String, start: usize },
}

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
    load_collapsed_with(app, text, ChatInput::load_full_text);
}

/// Like `load_collapsed`, for a message: it loads as a prompt.
pub fn load_collapsed_prompt(app: &mut App, text: String) {
    load_collapsed_with(app, text, ChatInput::load_prompt_text);
}

fn load_collapsed_with(app: &mut App, text: String, load: fn(&mut ChatInput, String)) {
    let display = app.chat_input.collapsed_pastes.display(&text);
    load(&mut app.chat_input, text);
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
        collapse_range(input, start, end, chars);
    }
}

/// Replace `start..end` of the input with the placeholder of a `chars`-character paste; returns its length.
pub fn collapse_range(input: &mut ChatInput, start: usize, end: usize, chars: usize) -> usize {
    let paste: Arc<str> = Arc::from(&input.input[start..end]);
    let label = crate::collapsed_pastes::label(chars);
    input.input.replace_range(start..end, &label);
    input.sync_mentions_at(start);
    input.collapsed_pastes.remember(paste.clone());
    input
        .mentions
        .add(&input.input, start, start + label.len(), Some(paste));
    label.len()
}

/// Undo the paste `text` just collapsed or turned into mentions; true when it did.
pub fn expand(app: &mut App, text: &str) -> bool {
    app.chat_input.sync_mentions();
    let input = &mut app.chat_input;
    let expanded = match input.expandable_paste.take() {
        Some(Offer::Collapsed(paste)) if *paste == *text => show_in_full(input, &paste),
        Some(Offer::Paths {
            paste,
            start,
            tokens,
        }) if paste == text => restore_literal(input, &paste, start, &tokens),
        _ => false,
    };
    dismiss(app);
    expanded
}

fn show_in_full(input: &mut ChatInput, paste: &Arc<str>) -> bool {
    let Some((start, end)) = input.mentions.span_of(paste) else {
        return false;
    };
    input.input.replace_range(start..end, paste);
    input.cursor = start + paste.len();
    input.anchor = None;
    input.sync_mentions_at(start);
    if !input.mentions.holds_paste(paste) {
        input.collapsed_pastes.forget(paste);
    }
    true
}

/// Swap the placeholders back for the pasted `literal`, as plain text.
fn restore_literal(input: &mut ChatInput, literal: &str, start: usize, tokens: &str) -> bool {
    let end = start + tokens.len();
    if input.input.get(start..end) != Some(tokens) {
        return false;
    }
    input.input.replace_range(start..end, literal);
    if input.cursor > start {
        input.cursor = input.cursor.max(end) - end + start + literal.len();
    }
    input.anchor = None;
    input.sync_mentions_at(start);
    true
}

/// Offer to keep as text the paths `text` pasted as the mentions at `start..end`.
pub fn offer_literal(app: &mut App, text: &str, (start, end): (usize, usize)) {
    let tokens = app.chat_input.input[start..end].to_owned();
    app.chat_input.expandable_paste = Some(Offer::Paths {
        paste: text.to_owned(),
        start,
        tokens,
    });
    crate::ui::notice::show(app, Severity::Information, LITERAL_HINT, HINT_SECS);
}

/// Mark the path list `raw` pasted at `start` as awaiting its probe.
pub fn await_probe(app: &mut App, raw: &str, start: usize) {
    app.chat_input.expandable_paste = Some(Offer::Probing {
        paste: raw.to_owned(),
        start,
    });
}

/// End the probe wait for `raw` at `start`; true when no edit came first.
pub fn end_probe(app: &mut App, raw: &str, start: usize) -> bool {
    let waiting = matches!(
        &app.chat_input.expandable_paste,
        Some(Offer::Probing { paste, start: at }) if *at == start && paste == raw
    );
    if waiting {
        app.chat_input.expandable_paste = None;
    }
    waiting
}

/// Follow `start..end` of the input becoming `len` bytes: a path offer after it moves, one it touches ends.
pub fn shift_offer(app: &mut App, (start, end, len): (usize, usize, usize)) {
    let input = &mut app.chat_input;
    if let Some(Offer::Paths {
        start: at, tokens, ..
    }) = &mut input.expandable_paste
    {
        if end <= *at {
            *at = *at - (end - start) + len;
        } else if start < *at + tokens.len() {
            input.expandable_paste = None;
        }
    }
}

/// End the paste-again offer: an edit makes the next paste a paste of its own.
pub fn dismiss(app: &mut App) {
    app.chat_input.expandable_paste = None;
    if app
        .overlays
        .notice
        .as_ref()
        .is_some_and(|notice| notice.text == HINT || notice.text == LITERAL_HINT)
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
    input.expandable_paste = Some(Offer::Collapsed(paste.clone()));
    input
        .mentions
        .add(&input.input, start, start + label.len(), Some(paste));
    crate::ui::notice::show(app, Severity::Information, HINT, HINT_SECS);
}
