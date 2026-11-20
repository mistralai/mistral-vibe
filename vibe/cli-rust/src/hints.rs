//! Shortcut-hint vocabulary: every footer names its keys and actions from here.

use unicode_width::UnicodeWidthStr;

/// A `(key, action)` pair; renderers own the spacing (see [`runs`]).
pub type Hint = (&'static str, &'static str);

/// Key names, written the same way on every screen.
pub mod key {
    pub const NAV: &str = "↑↓/jk";
    pub const ARROWS: &str = "↑↓";
    pub const UP: &str = "↑";
    pub const LEFT_RIGHT: &str = "←→";
    pub const LEFT_ESC: &str = "←/Esc";
    pub const RIGHT: &str = "→";
    pub const ENTER: &str = "Enter";
    pub const ENTER_S: &str = "Enter/s";
    pub const SPACE: &str = "Space";
    pub const SPACE_ENTER: &str = "Space/Enter";
    pub const YES_ENTER: &str = "y/Enter";
    pub const NO_ESC: &str = "n/Esc";
    pub const ESC: &str = "Esc";
    pub const ESC_CTRL_C: &str = "Esc/Ctrl+C";
    pub const TAB: &str = "Tab";
    /// Moves between a form's fields; Shift+Tab, implicit, moves back.
    pub const ARROWS_TAB: &str = "↑↓/Tab";
    pub const SEARCH: &str = "/";
    pub const BACKSPACE: &str = "Backspace";
    pub const BACKSPACE_CTRL_C: &str = "Backspace/Ctrl+C";
    pub const CTRL_C: &str = "Ctrl+C";
    pub const CTRL_J: &str = "Ctrl+J";
    pub const CTRL_X: &str = "Ctrl+X";
    pub const SHIFT_ARROWS: &str = "Shift+↑↓";
    pub const CTRL_R: &str = "Ctrl+R";
    pub const CTRL_S: &str = "Ctrl+S";
    pub const CTRL_BACKSLASH: &str = "Ctrl+\\";
    pub const PAGE_UP_DOWN: &str = "PgUp/PgDn";
    /// Switches tabs; Shift+Tab, implicit, switches back.
    pub const TAB_LEFT_RIGHT: &str = "Tab/←→";
}

/// Action names: lowercase verbs, shared across screens.
pub mod action {
    pub const NAVIGATE: &str = "navigate";
    pub const SELECT: &str = "select";
    pub const ACCEPT: &str = "accept";
    pub const OPEN: &str = "open";
    pub const FULL_CHANGELOG: &str = "full changelog";
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
    pub const WHY_BLOCKED: &str = "why blocked";
    pub const PREVIEW: &str = "preview";
    pub const SWITCH_BADGE: &str = "switch badge";
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
    pub const NEW: &str = "new";
    pub const CREATE: &str = "create";
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
    pub const ADD: &str = "add";
    pub const COMPLETE: &str = "complete";
    pub const TRUST: &str = "trust";
    pub const UNTRUST: &str = "untrust";
    pub const FORGET: &str = "forget";
    pub const CHANGE: &str = "change";
    pub const PIN: &str = "pin";
    pub const UNPIN: &str = "unpin";
    pub const PAUSE: &str = "pause";
    pub const RESUME: &str = "resume";
    pub const CLEAR: &str = "clear";
    pub const RESTORE_STASH: &str = "restore stash";
    pub const SWITCH_FIELD: &str = "switch field";
    pub const SWITCH_TAB: &str = "switch tab";
    pub const OPEN_CHECK: &str = "open check";
    pub const FIX: &str = "ask agent to fix";
    pub const DETAILS: &str = "details";
    pub const REVIEW: &str = "review";
    pub const ARCHIVE: &str = "archive";
    pub const UNARCHIVE: &str = "unarchive";
    pub const CLEAN_UP: &str = "clean up";
    pub const ALL: &str = "all";
    pub const SORT_SIZE: &str = "sort: size";
    pub const SORT_DATE: &str = "sort: date";
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

/// Hints packed into rows of at most `width` columns, breaking only between hints; a hint wider than a row gets a row of its own.
pub fn rows(hints: &[Hint], width: u16) -> Vec<Vec<Hint>> {
    let mut rows: Vec<Vec<Hint>> = Vec::new();
    for &hint in hints {
        match rows.last_mut() {
            Some(row) if self::width(row) + 2 + self::width(&[hint]) <= width => row.push(hint),
            _ => rows.push(vec![hint]),
        }
    }
    rows
}
