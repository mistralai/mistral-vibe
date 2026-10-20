//! Composer checkpoint batching, restoration, resource bounds, and platform shortcuts.

use std::time::{Duration, Instant};

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::{App, ChatInput};
use vibe_rs::chat_input::{apply, Action};
use vibe_rs::edit_history::{Snapshot, MAX_CHECKPOINTS, MAX_HISTORY_BYTES};
use vibe_rs::{input, keymap};

fn edit(input: &mut ChatInput, action: Action, now: Instant) {
    if !action.is_edit() {
        input.edit_history.checkpoint();
    }
    let before = Snapshot::capture(input);
    apply(
        &action,
        &mut input.input,
        &mut input.cursor,
        &mut input.anchor,
    );
    input.record_edit(before, false, now);
}

fn type_text(input: &mut ChatInput, text: &str, now: Instant) {
    for ch in text.chars() {
        edit(input, Action::Insert(ch), now);
    }
}

#[test]
fn typing_and_deletion_are_separate_batches() {
    let mut input = ChatInput::default();
    let now = Instant::now();
    type_text(&mut input, "helped", now);
    edit(&mut input, Action::DeleteLeft, now);
    edit(&mut input, Action::DeleteLeft, now);
    assert_eq!(input.input, "help");
    assert!(input.restore_edit(false));
    assert_eq!((input.input.as_str(), input.cursor), ("helped", 6));
    assert!(input.restore_edit(false));
    assert!(input.input.is_empty());
    assert!(!input.restore_edit(false));
    assert!(input.restore_edit(true));
    assert!(input.restore_edit(true));
    assert_eq!((input.input.as_str(), input.cursor), ("help", 4));
    assert!(!input.restore_edit(true));
}

#[test]
fn edits_after_undo_discard_redo_but_noops_do_not() {
    let mut input = ChatInput::default();
    let now = Instant::now();
    type_text(&mut input, "old", now);
    assert!(input.restore_edit(false));
    edit(&mut input, Action::DeleteLeft, now);
    assert!(input.restore_edit(true));
    assert!(input.restore_edit(false));
    type_text(&mut input, "new", now);
    assert!(!input.restore_edit(true));
    assert_eq!(input.input, "new");
}

#[test]
fn movement_pause_and_redo_start_new_batches() {
    let mut input = ChatInput::default();
    let now = Instant::now();
    type_text(&mut input, "ab", now);
    edit(&mut input, Action::CursorLeft, now);
    edit(&mut input, Action::Insert('X'), now);
    assert!(input.restore_edit(false));
    assert_eq!((input.input.as_str(), input.cursor), ("ab", 1));
    assert!(input.restore_edit(true));
    edit(&mut input, Action::Insert('Y'), now);
    assert!(input.restore_edit(false));
    assert_eq!(input.input, "aXb");
    edit(
        &mut input,
        Action::Insert('Z'),
        now + Duration::from_secs(3),
    );
    assert!(input.restore_edit(false));
    assert_eq!(input.input, "aXb");
}

#[test]
fn pause_separates_otherwise_contiguous_typing() {
    let mut input = ChatInput::default();
    let now = Instant::now();
    type_text(&mut input, "first", now);
    type_text(&mut input, "second", now + Duration::from_secs(3));
    assert!(input.restore_edit(false));
    assert_eq!(input.input, "first");
    assert!(input.restore_edit(false));
    assert!(input.input.is_empty());
}

#[test]
fn resetting_a_document_during_an_edit_does_not_record_the_replacement() {
    let mut input = ChatInput::default();
    type_text(&mut input, "draft", Instant::now());
    let before = Snapshot::capture(&input);
    input.load_full_text("recalled".to_owned());
    input.record_edit(before, true, Instant::now());
    assert!(!input.restore_edit(false));
    assert_eq!(input.input, "recalled");
}

#[test]
fn unicode_selection_and_multiline_paste_restore_caret_and_anchor() {
    let mut app = App::default();
    input::handle_paste(&mut app, "été 世界".to_owned());
    app.chat_input.anchor = Some(0);
    app.chat_input.cursor = "été".len();
    input::handle_paste(&mut app, "one\r\ntwo".to_owned());
    assert_eq!(app.chat_input.input, "one\ntwo 世界");
    assert!(app.chat_input.restore_edit(false));
    assert_eq!(app.chat_input.input, "été 世界");
    assert_eq!(app.chat_input.cursor, "été".len());
    assert_eq!(app.chat_input.anchor, Some(0));
    assert!(app.chat_input.restore_edit(true));
    assert_eq!(app.chat_input.input, "one\ntwo 世界");
    assert_eq!(app.chat_input.cursor, 7);
    assert_eq!(app.chat_input.anchor, None);
}

#[test]
fn newline_is_isolated_from_typing_on_either_side() {
    let mut input = ChatInput::default();
    type_text(&mut input, "top\nbottom", Instant::now());
    assert!(input.restore_edit(false));
    assert_eq!(input.input, "top\n");
    assert!(input.restore_edit(false));
    assert_eq!(input.input, "top");
    assert!(input.restore_edit(false));
    assert_eq!(input.input, "");
}

#[test]
fn clear_and_recall_reset_both_stacks() {
    let mut input = ChatInput::default();
    let now = Instant::now();
    type_text(&mut input, "submitted", now);
    input.clear();
    assert!(!input.restore_edit(false));
    type_text(&mut input, "draft", now);
    assert!(input.restore_edit(false));
    input.load_full_text("!recalled".to_owned());
    assert!(!input.restore_edit(true));
    assert!(!input.restore_edit(false));
    type_text(&mut input, " edit", now);
    assert!(input.restore_edit(false));
    assert_eq!(input.full_text(), "!recalled");
}

#[test]
fn unrecorded_replacements_never_restore_an_unrelated_draft() {
    let mut input = ChatInput::default();
    type_text(&mut input, "old", Instant::now());
    input.input = "external".to_owned();
    assert!(!input.restore_edit(false));
    assert_eq!(input.input, "external");
}

#[test]
fn batch_character_limit_and_checkpoint_limit_evict_oldest() {
    let mut input = ChatInput::default();
    type_text(&mut input, &"x".repeat(101), Instant::now());
    assert!(input.restore_edit(false));
    assert_eq!(input.input.len(), 100);
    input.clear();
    for _ in 0..MAX_CHECKPOINTS + 5 {
        input.edit_history.checkpoint();
        type_text(&mut input, "x", Instant::now());
    }
    for _ in 0..MAX_CHECKPOINTS {
        assert!(input.restore_edit(false));
    }
    assert!(!input.restore_edit(false));
    assert_eq!(input.input, "xxxxx");
    for _ in 0..MAX_CHECKPOINTS {
        assert!(input.restore_edit(true));
    }
    assert!(!input.restore_edit(true));
}

#[test]
fn byte_budget_evicts_old_checkpoints_and_preserves_redo() {
    let mut input = ChatInput::default();
    let size = MAX_HISTORY_BYTES / 4;
    input.load_full_text("a".repeat(size));
    for ch in ['b', 'c', 'd'] {
        let before = Snapshot::capture(&input);
        input.input = ch.to_string().repeat(size);
        input.record_edit(before, true, Instant::now());
    }
    assert!(input.restore_edit(false));
    assert!(input.restore_edit(false));
    assert!(!input.restore_edit(false));
    assert_eq!(input.input, "b".repeat(size));
    assert!(input.restore_edit(true));
    assert!(input.restore_edit(true));
    assert_eq!(input.input, "d".repeat(size));
}

#[test]
fn byte_budget_drops_oversized_checkpoints() {
    let mut app = App::default();
    let before = Snapshot::capture(&app.chat_input);
    app.chat_input.input = "x".repeat(MAX_HISTORY_BYTES + 1);
    app.chat_input.cursor = app.chat_input.input.len();
    app.chat_input.record_edit(before, true, Instant::now());
    assert!(!app.chat_input.restore_edit(false));
    assert_eq!(app.chat_input.input.len(), MAX_HISTORY_BYTES + 1);
}

#[test]
fn shortcuts_preserve_unix_suspend_and_copy_and_enable_ctrl_elsewhere() {
    for can_suspend in [true, false] {
        for (ch, action) in [('z', Action::Undo), ('y', Action::Redo)] {
            let command = KeyEvent::new(KeyCode::Char(ch), KeyModifiers::SUPER);
            assert_eq!(
                keymap::history_action_for(&command, can_suspend),
                Some(action.clone())
            );
            let ctrl = KeyEvent::new(KeyCode::Char(ch), KeyModifiers::CONTROL);
            assert_eq!(
                keymap::history_action_for(&ctrl, can_suspend),
                (!can_suspend).then_some(action.clone())
            );
            assert_eq!(keymap::action_for(&ctrl), (!cfg!(unix)).then_some(action));
            for modifiers in [
                KeyModifiers::NONE,
                KeyModifiers::ALT,
                KeyModifiers::SUPER | KeyModifiers::SHIFT,
                KeyModifiers::CONTROL | KeyModifiers::ALT,
            ] {
                assert_eq!(
                    keymap::history_action_for(
                        &KeyEvent::new(KeyCode::Char(ch), modifiers),
                        can_suspend
                    ),
                    None
                );
            }
        }
    }
}
