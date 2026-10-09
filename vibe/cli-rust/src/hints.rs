//! Shortcut-hint vocabulary: every footer names its keys and actions from here.

use unicode_width::UnicodeWidthStr;

/// A `(key, action)` pair; renderers own the spacing (see [`runs`]).
pub type Hint = (&'static str, &'static str);

/// Key names, written the same way on every screen.
pub mod key {
    pub const NAV: &str = "↑↓/jk";
    pub const ARROWS: &str = "↑↓";
    pub const LEFT_RIGHT: &str = "←→";
    pub const LEFT_ESC: &str = "←/Esc";
    pub const RIGHT: &str = "→";
    pub const ENTER: &str = "Enter";
    pub const ENTER_S: &str = "Enter/s";
    pub const SPACE_ENTER: &str = "Space/Enter";
    pub const ESC: &str = "Esc";
    pub const ESC_CTRL_C: &str = "Esc/Ctrl+C";
    pub const TAB: &str = "Tab";
    pub const SEARCH: &str = "/";
    pub const BACKSPACE: &str = "Backspace";
    pub const BACKSPACE_CTRL_C: &str = "Backspace/Ctrl+C";
    pub const CTRL_C: &str = "Ctrl+C";
    pub const CTRL_J: &str = "Ctrl+J";
    pub const CTRL_R: &str = "Ctrl+R";
    pub const CTRL_S: &str = "Ctrl+S";
}

/// Action names: lowercase verbs, shared across screens.
pub mod action {
    pub const NAVIGATE: &str = "navigate";
    pub const SELECT: &str = "select";
    pub const SAVE: &str = "save";
    pub const SESSION_ONLY: &str = "session only";
    pub const CANCEL: &str = "cancel";
    pub const CLOSE: &str = "close";
    pub const BACK: &str = "back";
    pub const EXIT: &str = "exit";
    pub const QUIT: &str = "quit";
    pub const EDIT: &str = "edit";
    pub const RESET: &str = "reset";
    pub const SEARCH: &str = "search";
    pub const LEAVE_SEARCH: &str = "leave search";
    pub const CLEAR_SEARCH: &str = "clear search";
    pub const TOGGLE: &str = "toggle";
    pub const VIEW: &str = "view";
    pub const RELOAD: &str = "reload";
    pub const REFRESH: &str = "refresh";
    pub const RETRY: &str = "retry";
    pub const DELETE: &str = "delete";
    pub const DISABLE: &str = "disable";
    pub const ENABLE: &str = "enable";
    pub const SHOW_TOOLS: &str = "show tools";
    pub const CONNECT: &str = "connect";
    pub const PREVIEW: &str = "preview";
    pub const SWITCH_BADGE: &str = "switch badge";
    pub const CREATE: &str = "create";
    pub const SAVE_EXIT: &str = "save & exit";
    pub const CHANGE_LAYER: &str = "change layer";
    pub const LAYER: &str = "layer";
    pub const QUESTIONS: &str = "questions";
    pub const NEWLINE: &str = "newline";
    pub const REJECT: &str = "reject";
    pub const PICK_OPTION: &str = "pick option";
    pub const CONFIRM: &str = "confirm";
    pub const PREVIOUS: &str = "previous";
    pub const NEXT: &str = "next";
    pub const SCROLL: &str = "scroll";
    pub const SUBMIT: &str = "submit";
    pub const CONTINUE: &str = "continue";
    pub const API_KEY: &str = "enter API key manually";
    pub const INTERRUPT: &str = "interrupt";
    pub const STEER: &str = "steer";
    pub const STOP: &str = "stop";
    pub const CANCEL_LAST_QUEUED: &str = "cancel last queued message";
    pub const REMOVE: &str = "remove";
    pub const DISCARD: &str = "discard";
    pub const SUBMIT_AS_NEW: &str = "submit as new";
}

pub const NAVIGATE: Hint = (key::NAV, action::NAVIGATE);
pub const SELECT: Hint = (key::ENTER, action::SELECT);
pub const SEARCH: Hint = (key::SEARCH, action::SEARCH);
pub const CANCEL: Hint = (key::ESC, action::CANCEL);
pub const CLOSE: Hint = (key::ESC, action::CLOSE);
pub const BACK: Hint = (key::ESC, action::BACK);

/// A hint row as `(text, is_key)` runs: `key action` pairs, two spaces apart.
pub fn runs(hints: &[Hint]) -> Vec<(&'static str, bool)> {
    let mut runs = Vec::with_capacity(hints.len() * 4);
    for (index, &(key, action)) in hints.iter().enumerate() {
        if index > 0 {
            runs.push(("  ", false));
        }
        runs.extend([(key, true), (" ", false), (action, false)]);
    }
    runs
}

/// The rendered width of a hint row.
pub fn width(hints: &[Hint]) -> u16 {
    runs(hints)
        .iter()
        .map(|(text, _)| text.width() as u16)
        .sum()
}
