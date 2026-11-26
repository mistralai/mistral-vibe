//! Opt-in vim Normal and Insert modes for the composer (`vim_keybindings`).

mod edit;
pub mod motion;
mod operator;
mod route;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::Value;

use crate::app::{App, ChatInput};
use crate::chat_input::Action;
use crate::utils::input_edit::line_bounds;
pub use edit::caret;
use edit::{delete, move_caret, past_mention, settle};
use motion::{first_non_blank, last_char, left, next_word_start, prev_word_start, right, word_end};
use operator::operator;
pub use route::{owns, route, take_pending};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Mode {
    #[default]
    Insert,
    Normal,
}

#[derive(Debug, Default)]
pub struct Vim {
    pub enabled: bool,
    pub mode: Mode,
    /// The operator (`d`, `c`) or prefix (`g`) waiting for its second key.
    pub pending: Option<char>,
    /// Normal mode was entered since vim mode turned on; its first entry is recorded once.
    pub used: bool,
}

/// What the composer does with one key: move, delete, undo or redo, handle `keys` as if pressed, then move.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Plan {
    pub caret: Option<usize>,
    /// The byte range deleted as one undoable edit.
    pub delete: Option<(usize, usize)>,
    /// `Action::Undo` or `Action::Redo`.
    pub restore: Option<Action>,
    pub keys: Vec<KeyEvent>,
    /// The caret after `keys`.
    pub after: Option<usize>,
}

impl Plan {
    fn keys(keys: Vec<KeyEvent>) -> Self {
        Self {
            keys,
            ..Self::default()
        }
    }

    fn caret(at: usize) -> Self {
        Self {
            caret: Some(at),
            ..Self::default()
        }
    }

    fn delete(from: usize, to: usize) -> Self {
        Self {
            delete: (from != to).then(|| (from.min(to), from.max(to))),
            ..Self::default()
        }
    }

    fn restore(action: Action) -> Self {
        Self {
            restore: Some(action),
            ..Self::default()
        }
    }
}

fn press(code: KeyCode, modifiers: KeyModifiers) -> KeyEvent {
    KeyEvent::new(code, modifiers)
}

fn newline() -> KeyEvent {
    press(KeyCode::Char('j'), KeyModifiers::CONTROL)
}

/// Handle `key` in vim mode, passing the composer keys it maps to to `run`; true means exit.
pub fn handle(
    app: &mut App,
    key: KeyEvent,
    mut run: impl FnMut(&mut App, KeyEvent) -> bool,
) -> bool {
    app.chat_input.normalize_positions();
    app.chat_input.sync_mentions();
    app.chat_input.last_keystroke = Some(crate::clock::now());
    // Whatever moved the caret last, a Normal-mode key starts from a character.
    settle(app);
    if app.vim.mode == Mode::Normal || key.code == KeyCode::Esc {
        // Each Normal-mode command, and each Insert session, is its own undo step.
        app.chat_input.edit_history.checkpoint();
    }
    if app.vim.mode == Mode::Normal
        && app.feedback.message == crate::feedback::Message::Prompt
        && matches!(key.code, KeyCode::Char(_))
        && !key
            .modifiers
            .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER)
    {
        // Editing dismisses the feedback prompt, as typing does in Insert mode.
        crate::feedback::hide(app);
    }
    let submit = app.vim.mode == Mode::Normal
        && key.code == KeyCode::Enter
        && key.modifiers.is_empty()
        && !app.chat_input.full_text().is_empty();
    if app.vim.mode == Mode::Normal
        && key.code == KeyCode::Esc
        && !app.chat_input.full_text().is_empty()
    {
        // Esc in Normal mode never arms the double Esc that clears the draft.
        app.overlays.last_escape = None;
    }
    let mode = app.vim.mode;
    let plan = plan(&mut app.vim, &app.chat_input, key);
    // The change that opens an Insert session and its typing undo as one step.
    if app.vim.mode == Mode::Insert {
        app.chat_input.edit_history.begin_group();
    } else if mode == Mode::Insert {
        app.chat_input.edit_history.end_group();
    }
    if app.vim.mode == Mode::Normal && !app.vim.used {
        app.vim.used = true;
        crate::telemetry::vim_normal_mode_entered(app);
    }
    if let Some(to) = plan.caret {
        move_caret(app, to);
    }
    if let Some(span) = plan.delete {
        delete(app, span);
    }
    if let Some(action) = plan.restore {
        crate::composer_history::restore(app, &action);
    }
    if plan.keys.into_iter().any(|key| run(app, key)) {
        return true;
    }
    if let Some(to) = plan.after {
        move_caret(app, to);
    }
    if submit && app.chat_input.full_text().is_empty() {
        app.vim.mode = Mode::Insert;
    }
    false
}

/// Vim Insert mode, labelled on the composer and drawn with a bar caret; Normal mode shows neither.
pub fn inserting(app: &App) -> bool {
    app.vim.enabled && app.vim.mode == Mode::Insert
}

/// Follow `vim_keybindings` from a runtime snapshot; a toggle starts in Insert mode.
pub fn apply_runtime(app: &mut App, runtime: &Value) {
    let enabled = crate::startup::read_vim_keybindings(runtime);
    if enabled != app.vim.enabled {
        app.chat_input.edit_history.end_group();
        app.vim = Vim {
            enabled,
            ..Vim::default()
        };
    }
}

pub fn plan(vim: &mut Vim, chat: &ChatInput, key: KeyEvent) -> Plan {
    let (input, cursor) = (chat.input.as_str(), chat.cursor);
    match vim.mode {
        Mode::Insert if key.code == KeyCode::Esc => {
            vim.mode = Mode::Normal;
            // Leaving Insert puts the caret back on the character it followed.
            let start = line_bounds(input, cursor).0;
            Plan::caret(if cursor > start {
                left(input, cursor)
            } else {
                cursor
            })
        }
        Mode::Insert => Plan::keys(vec![key]),
        Mode::Normal => normal(vim, chat, key),
    }
}

fn normal(vim: &mut Vim, chat: &ChatInput, key: KeyEvent) -> Plan {
    if key.modifiers == KeyModifiers::CONTROL && key.code == KeyCode::Char('r') {
        vim.pending = None;
        return Plan::restore(Action::Redo);
    }
    let plain = (key.modifiers - KeyModifiers::SHIFT).is_empty();
    let c = match (key.code, plain) {
        (KeyCode::Char(c), true) => c,
        // Backspace (Ctrl+H on some terminals) moves left like `h` instead of deleting.
        (KeyCode::Backspace, true) => 'h',
        (KeyCode::Char('h'), _) if key.modifiers == KeyModifiers::CONTROL => 'h',
        _ => {
            // Enter after an operator is a slip, never a submit.
            if vim.pending.take().is_some() && matches!(key.code, KeyCode::Esc | KeyCode::Enter) {
                return Plan::default();
            }
            return Plan::keys(vec![key]);
        }
    };
    if let Some(op) = vim.pending.take() {
        return operator(vim, op, c, chat);
    }
    let (input, cursor) = (chat.input.as_str(), chat.cursor);
    let (start, end) = line_bounds(input, cursor);
    match c {
        'h' if cursor > start => Plan::caret(left(input, cursor)),
        'l' if cursor < last_char(input, cursor) => Plan::caret(right(input, cursor)),
        'j' => Plan::keys(vec![press(KeyCode::Down, KeyModifiers::NONE)]),
        'k' => Plan::keys(vec![press(KeyCode::Up, KeyModifiers::NONE)]),
        'w' => Plan::caret(past_mention(chat, 'w', next_word_start(input, cursor))),
        'b' => Plan::caret(prev_word_start(input, cursor)),
        'e' => Plan::caret(past_mention(chat, 'e', word_end(input, cursor))),
        '0' => Plan::caret(start),
        '^' => Plan::caret(first_non_blank(input, cursor)),
        '$' => Plan::caret(last_char(input, cursor)),
        'G' => Plan::caret(first_non_blank(input, input.len())),
        'g' | 'd' | 'c' => {
            vim.pending = Some(c);
            Plan::default()
        }
        'x' if cursor < end => Plan::delete(cursor, right(input, cursor)),
        'X' if cursor > start => Plan::delete(cursor, left(input, cursor)),
        'D' => Plan::delete(cursor, end),
        'C' => insert(vim, Plan::delete(cursor, end)),
        'u' => Plan::restore(Action::Undo),
        'i' => insert(vim, Plan::default()),
        'a' if cursor < end => insert(vim, Plan::caret(right(input, cursor))),
        'a' => insert(vim, Plan::default()),
        'I' => insert(vim, Plan::caret(first_non_blank(input, cursor))),
        'A' => insert(vim, Plan::caret(end)),
        'o' => insert(
            vim,
            Plan {
                caret: Some(end),
                keys: vec![newline()],
                ..Plan::default()
            },
        ),
        'O' => insert(
            vim,
            Plan {
                caret: Some(start),
                keys: vec![newline(), press(KeyCode::Left, KeyModifiers::NONE)],
                ..Plan::default()
            },
        ),
        // An empty draft opens slash and shell modes, as in Insert mode.
        '/' | '!' if input.is_empty() => insert(vim, Plan::keys(vec![key])),
        _ => Plan::default(),
    }
}

fn insert(vim: &mut Vim, plan: Plan) -> Plan {
    vim.mode = Mode::Insert;
    plan
}
