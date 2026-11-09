//! Vim Normal-mode operators, deletes and undo or redo in the composer.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::vim::Mode;

use crate::support::{ctrl, send_key};
use crate::vim::{app, assert_edit, run, type_keys};

#[test]
fn the_normal_caret_stays_on_a_character_after_edits() {
    assert_edit("abc", 2, "xx", ("a", 0));
    assert_edit("hello world", 6, "Dx", ("hello", 4));
    assert_edit("hello world", 6, "d$", ("hello ", 5));
    assert_edit("ab cd\nef", 3, "dw", ("ab \nef", 2));
    assert_edit("abcdef\nxy", 9, "ggx", ("bcdef\nxy", 0));
}

#[test]
fn operators_delete_their_motion_span() {
    let edit = |cursor, keys| run("hello big world", cursor, keys).0;
    assert_eq!(edit(0, "dw"), "big world");
    assert_eq!(edit(0, "de"), " big world");
    assert_eq!(edit(6, "db"), "big world");
    assert_eq!(edit(6, "D"), "hello ");
    assert_eq!(edit(0, "x"), "ello big world");
    assert_eq!(edit(0, "X"), "hello big world");
    assert_eq!(edit(0, "dwu"), "hello big world");
    assert_eq!(run("ab\ncd", 1, "de").0, "a");
    assert_eq!(run("ab  ", 1, "de").0, "a");
    assert_eq!(run("a\n\nb", 2, "dw").0, "a\nb");
}

#[test]
fn delete_back_a_word_from_a_line_start_keeps_the_line_break() {
    assert_eq!(run("x ab\ncd", 5, "db").0, "x \ncd");
    assert_eq!(run("ab ef\ncd", 6, "db").0, "ab \ncd");
    assert_eq!(run("  ab  \ncd", 7, "db").0, "cd");
    assert_eq!(run("a\n\ncd", 3, "db").0, "a\ncd");
    assert_eq!(run("x ab\ncd", 5, "cbZ").0, "x Z\ncd");
    assert_eq!(run("  ab  \ncd", 7, "cbZ").0, "Z\ncd");
    assert_eq!(run("a\n\ncd", 3, "cbZ").0, "a\nZ\ncd");
}

#[test]
fn undo_restores_the_text_without_a_selection() {
    assert_eq!(run("hello world", 0, "dwuiZ").0, "Zhello world");
    assert_eq!(run("abc", 0, "xuiZ").0, "Zabc");
    let mut normal = app(Mode::Normal, "one\ntwo\nthree", 4);
    assert_eq!(type_keys(&mut normal, "ddddu").0, "one\nthree");
    assert_eq!(normal.chat_input.anchor, None);
    let mut insert = app(Mode::Normal, "hello world", 0);
    type_keys(&mut insert, "cw");
    send_key(
        &mut insert,
        KeyEvent::new(KeyCode::Char('z'), KeyModifiers::SUPER),
    );
    assert_eq!(type_keys(&mut insert, "X").0, "Xhello world");
}

#[test]
fn undo_reverts_one_normal_command_or_insert_session() {
    assert_eq!(run("one two three", 0, "dwdwu").0, "two three");
    assert_eq!(run("abc", 0, "xxu").0, "bc");
    let mut insert = app(Mode::Insert, "", 0);
    assert_eq!(type_keys(&mut insert, "abc⎋Adef⎋u").0, "abc");
}

#[test]
fn undo_reverts_a_change_with_the_text_typed_after_it() {
    assert_eq!(run("hello world", 0, "cwhi⎋u").0, "hello world");
    assert_eq!(run("hello world", 0, "Chi⎋u").0, "hello world");
    assert_eq!(run("ab", 0, "oxy⎋u").0, "ab");
    assert_eq!(run("ab", 0, "Oxy⎋u").0, "ab");
    assert_eq!(run("hello world", 0, "cwhi⎋uiZ").0, "Zhello world");
    let mut insert = app(Mode::Insert, "", 0);
    assert_eq!(type_keys(&mut insert, "abx⌫c⎋u").0, "");
    let mut redo = app(Mode::Normal, "hello world", 0);
    type_keys(&mut redo, "cwhi⎋u");
    send_key(&mut redo, ctrl('r'));
    assert_eq!(redo.chat_input.input, "hi world");
}

#[test]
fn undo_and_redo_in_insert_mode_do_not_fold_earlier_edits_into_the_session() {
    let super_key = |c| KeyEvent::new(KeyCode::Char(c), KeyModifiers::SUPER);
    let mut insert = app(Mode::Insert, "one two three", 13);
    type_keys(&mut insert, "⎋0dwdwA x");
    for c in "zzzyy".chars() {
        send_key(&mut insert, super_key(c));
    }
    assert_eq!(insert.chat_input.input, "three");
    assert_eq!(type_keys(&mut insert, "y⎋u").0, "three");
}

#[test]
fn ctrl_r_redoes_in_normal_mode_even_with_voice_mode() {
    let mut normal = app(Mode::Normal, "hello world", 0);
    normal.voice.mode_enabled = true;
    type_keys(&mut normal, "dwu");
    send_key(&mut normal, ctrl('r'));
    assert_eq!(normal.chat_input.input, "world");
    assert!(normal.reverse_search.is_none());
}

#[test]
fn ctrl_r_cancels_a_pending_operator() {
    let mut normal = app(Mode::Normal, "hello world", 0);
    type_keys(&mut normal, "d");
    send_key(&mut normal, ctrl('r'));
    assert_eq!(type_keys(&mut normal, "w"), ("hello world".to_owned(), 6));
}

#[test]
fn delete_line_removes_one_newline_and_lands_on_the_first_non_blank() {
    assert_edit("one\ntwo", 0, "dd", ("two", 0));
    assert_edit("one\ntwo", 4, "dd", ("one", 0));
    assert_edit("abcdef\n  two", 5, "dd", ("  two", 2));
    assert_edit("a\n  b\nc", 0, "jdd", ("a\nc", 2));
    assert_edit("one\n", 4, "dd", ("one", 0));
    assert_edit("only", 2, "dd", ("", 0));
}

#[test]
fn change_operators_delete_then_insert() {
    let mut normal = app(Mode::Normal, "hello big", 0);
    assert_eq!(type_keys(&mut normal, "cw").0, " big");
    assert_eq!(normal.vim.mode, Mode::Insert);
    assert_eq!(run("ab cd", 1, "cwX").0, "aX cd");
    assert_eq!(run("ab   cd", 2, "cwX").0, "abXcd");
    assert_eq!(run("hello big", 6, "cc").0, "");
    assert_eq!(run("hello big", 2, "C").0, "he");
}

#[test]
fn delete_to_first_non_blank_on_a_blank_line_keeps_its_last_blank() {
    assert_eq!(run("   ", 0, "d^").0, " ");
    assert_eq!(run("   ", 0, "c^").0, " ");
    assert_eq!(run("  hi", 3, "d^").0, "  i");
}

#[test]
fn word_motions_treat_a_paste_placeholder_as_one_word() {
    let text = "ab [Pasted 1200 characters] cd";
    let placed = |keys| {
        let mut normal = app(Mode::Normal, text, 0);
        normal.chat_input.add_mention(3, 27);
        type_keys(&mut normal, keys)
    };
    assert_eq!(placed("ww").1, 28);
    assert_eq!(placed("eee").1, 29);
    assert_eq!(placed("wwx").0, "ab [Pasted 1200 characters] d");
}

#[test]
fn delete_word_takes_a_mention_and_its_trailing_blank() {
    let deleted = |text: &str, end, keys| {
        let mut normal = app(Mode::Normal, text, 0);
        normal.chat_input.add_mention(0, end);
        type_keys(&mut normal, keys).0
    };
    assert_eq!(deleted("@a.txt hello", 6, "dw"), "hello");
    assert_eq!(deleted("[Pasted 1200 characters] cd", 24, "dw"), "cd");
    assert_eq!(deleted("@a.txt\nhello", 6, "dw"), "\nhello");
    assert_eq!(deleted("@a.txt hello", 6, "cwX"), "X hello");
}
