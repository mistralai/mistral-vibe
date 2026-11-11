//! Shared list search: `/` focuses, typing filters, ↑↓/Enter reach the list, Esc leaves then clears.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::Rect;

use crate::hints::{action, key, Hint};
use crate::{chat_input, keymap};

/// Ignore excess input beyond this UTF-8 byte bound.
pub const MAX_QUERY_BYTES: usize = 1024;

#[derive(Default)]
pub struct Search {
    pub query: String,
    pub cursor: usize,
    pub anchor: Option<usize>,
    pub focused: bool,
    pub area: Rect,
}

/// What a key did to the search.
#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Not a search key: the list handles it.
    Pass,
    /// Focus or caret moved; the filtered rows are unchanged.
    Consumed,
    /// The query changed; the list resets its highlight.
    Filtered,
}

/// Route a key: the list keeps ↑↓, PageUp/PageDown and Enter even while the field is focused.
pub fn handle_key(search: &mut Search, key: &KeyEvent) -> Outcome {
    if !search.focused {
        return handle_list_key(search, key);
    }
    match key.code {
        KeyCode::Esc => {
            search.focused = false;
            search.anchor = None;
            Outcome::Consumed
        }
        KeyCode::Up | KeyCode::Down | KeyCode::PageUp | KeyCode::PageDown | KeyCode::Enter => {
            Outcome::Pass
        }
        _ => match keymap::action_for(key).map(|action| edit(search, action)) {
            Some(true) => Outcome::Filtered,
            _ => Outcome::Consumed,
        },
    }
}

/// With the list focused: `/` focuses the field, Esc clears a lingering filter.
fn handle_list_key(search: &mut Search, key: &KeyEvent) -> Outcome {
    let chorded = key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    match key.code {
        // Shift is how some layouts type `/`.
        KeyCode::Char('/') if !chorded => {
            search.focused = true;
            Outcome::Consumed
        }
        KeyCode::Esc if !search.query.is_empty() => {
            clear(search);
            Outcome::Filtered
        }
        _ => Outcome::Pass,
    }
}

/// Drop the query and leave the field.
pub fn clear(search: &mut Search) {
    let area = search.area;
    *search = Search {
        area,
        ..Search::default()
    };
}

pub fn edit(search: &mut Search, action: chat_input::Action) -> bool {
    if let chat_input::Action::Insert(ch) = action {
        if ch.is_control() {
            return false;
        }
        let selected = chat_input::selection_range(&search.query, search.cursor, search.anchor)
            .map_or(0, |(start, end)| end - start);
        if search.query.len() - selected + ch.len_utf8() > MAX_QUERY_BYTES {
            return false;
        }
    }
    chat_input::apply(
        &action,
        &mut search.query,
        &mut search.cursor,
        &mut search.anchor,
    );
    action.is_edit()
}

/// Insert pasted text into the focused field; false when it is not focused.
pub fn paste(search: &mut Search, text: &str) -> bool {
    if !search.focused {
        return false;
    }
    for ch in text
        .chars()
        .take(MAX_QUERY_BYTES)
        .filter(|ch| !ch.is_control())
    {
        edit(search, chat_input::Action::Insert(ch));
    }
    true
}

/// What a left press in a browser with a search row landed on.
#[derive(Debug, PartialEq, Eq)]
pub enum Press {
    /// The field, which it focused.
    Field,
    /// An option of the list, which took focus from the field.
    Option,
    Other,
}

/// Focus the field on a press on it while `shown`, and leave it on a press on an option.
pub fn press(search: &mut Search, event: &MouseEvent, shown: bool, on_option: bool) -> Press {
    if event.kind != MouseEventKind::Down(MouseButton::Left) {
        return Press::Other;
    }
    if shown && search.area.contains((event.column, event.row).into()) {
        search.focused = true;
        return Press::Field;
    }
    if !on_option {
        return Press::Other;
    }
    search.focused = false;
    Press::Option
}

/// Footer hints for the search state: typing keys hide while focused, and Esc says what it does.
pub fn hints(focused: bool, filtered: bool, list: &[Hint]) -> Vec<Hint> {
    let esc = match (focused, filtered) {
        (true, _) => action::LEAVE_SEARCH,
        (false, true) => action::CLEAR_SEARCH,
        (false, false) => return list.to_vec(),
    };
    list.iter()
        .filter(|(name, _)| !focused || !types_into_field(name))
        .map(|&(name, label)| match name {
            key::NAV if focused => (key::ARROWS, label),
            key::ESC => (name, esc),
            _ => (name, label),
        })
        .collect()
}

/// Keys the focused field consumes as text: letters, `/`, Space and Backspace.
fn types_into_field(name: &str) -> bool {
    name.chars().count() == 1 || name == key::BACKSPACE || name == key::SPACE
}
