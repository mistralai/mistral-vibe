//! The crossterm-to-editing boundary: map a key to a chat input `Action`.
//!
//! This is the only module that reads crossterm key events for editing, so the
//! `input_edit` domain stays backend-agnostic. Bindings mirror Textual's
//! TextArea plus vibe's Alt-word aliases.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

use crate::chat_input::Action;

/// The chat input editing action a key triggers, or `None` for keys the chat input
/// does not edit with (submit, history, scroll, popup nav are handled elsewhere).
pub fn action_for(k: &KeyEvent) -> Option<Action> {
    let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
    let alt = k.modifiers.contains(KeyModifiers::ALT);
    let shift = k.modifiers.contains(KeyModifiers::SHIFT);
    // Ctrl or Alt turns caret/delete keys into their word-wise variant.
    let word = ctrl || alt;
    match k.code {
        // Newline insert: Textual binds `shift+enter,ctrl+j`. In raw mode crossterm
        // delivers Ctrl+J (byte \n) as Ctrl+'j'; Shift+Enter only reaches us on
        // terminals that report it distinctly (plain Enter stays a submit).
        KeyCode::Char('j') if ctrl && !alt => Some(Action::Insert('\n')),
        KeyCode::Enter if shift => Some(Action::Insert('\n')),
        // Ctrl+letter is a shortcut, never literal text (Textual line-editing
        // bindings); Ctrl+C / Ctrl+D are intercepted before we get here.
        KeyCode::Char('a') if ctrl && !alt => Some(Action::CursorLineStart),
        KeyCode::Char('e') if ctrl && !alt => Some(Action::CursorLineEnd),
        KeyCode::Char('u') if ctrl && !alt => Some(Action::DeleteToStartOfLine),
        KeyCode::Char('k') if ctrl && shift && !alt => Some(Action::DeleteLine),
        KeyCode::Char('k') if ctrl && !alt => Some(Action::DeleteToEndOfLine),
        KeyCode::Char('w') if ctrl && !alt => Some(Action::DeleteWordLeft),
        // Alt+b / Alt+f: emacs word nav. macOS terminals (Terminal.app, iTerm
        // "natural editing") send Option+Left/Right as ESC-b / ESC-f, which
        // crossterm decodes as Alt+'b' / Alt+'f'. Textual maps the same bytes to
        // Ctrl+Left / Ctrl+Right (see its _ansi_sequences), so mirror that.
        KeyCode::Char('b') if alt && !ctrl => Some(Action::CursorWordLeft),
        KeyCode::Char('f') if alt && !ctrl => Some(Action::CursorWordRight),
        // Any other Ctrl/Alt-modified char has no chat input binding; swallow it so
        // it does not self-insert.
        KeyCode::Char(_) if ctrl || alt => None,
        KeyCode::Char(c) => Some(Action::Insert(c)),
        KeyCode::Backspace if word => Some(Action::DeleteWordLeft),
        KeyCode::Backspace => Some(Action::DeleteLeft),
        KeyCode::Delete if word => Some(Action::DeleteWordRight),
        KeyCode::Delete => Some(Action::DeleteRight),
        // Shift extends a selection; Shift+Ctrl/Alt extends by word (Textual
        // `cursor_*(select=True)`). Shift+Up/Down are left unmapped so the app
        // handles them as scroll.
        KeyCode::Left if shift && word => Some(Action::SelectWordLeft),
        KeyCode::Left if shift => Some(Action::SelectLeft),
        KeyCode::Right if shift && word => Some(Action::SelectWordRight),
        KeyCode::Right if shift => Some(Action::SelectRight),
        KeyCode::Home if shift => Some(Action::SelectLineStart),
        KeyCode::End if shift => Some(Action::SelectLineEnd),
        KeyCode::F(7) => Some(Action::SelectAll),
        KeyCode::F(6) => Some(Action::SelectLine),
        KeyCode::Left if word => Some(Action::CursorWordLeft),
        KeyCode::Left => Some(Action::CursorLeft),
        KeyCode::Right if word => Some(Action::CursorWordRight),
        KeyCode::Right => Some(Action::CursorRight),
        KeyCode::Home => Some(Action::CursorLineStart),
        KeyCode::End => Some(Action::CursorLineEnd),
        _ => None,
    }
}
