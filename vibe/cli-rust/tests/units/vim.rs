//! Vim Normal and Insert modes: motions and mode switches in the composer.

use crossterm::event::{KeyCode, KeyEvent, KeyEventState, KeyModifiers};
use serde_json::json;
use vibe_rs::app::{App, ChatInput};
use vibe_rs::chat_input::Action;
use vibe_rs::event_handler::apply_runtime_value;
use vibe_rs::feedback::Message;
use vibe_rs::paste_image;
use vibe_rs::telemetry;
use vibe_rs::vim::motion::{
    first_non_blank, last_char, next_word_start, prev_word_start, word_end,
};
use vibe_rs::vim::{caret, inserting, plan, Mode, Plan, Vim};

use crate::support::{ctrl, key, send_key};

fn normal() -> Vim {
    Vim {
        enabled: true,
        mode: Mode::Normal,
        ..Vim::default()
    }
}

fn chat(text: &str) -> ChatInput {
    ChatInput {
        input: text.to_owned(),
        ..ChatInput::default()
    }
}

pub(crate) fn app(mode: Mode, text: &str, cursor: usize) -> App {
    let mut app = App {
        vim: Vim { mode, ..normal() },
        ..App::default()
    };
    app.chat_input.input = text.to_owned();
    app.chat_input.cursor = cursor;
    app
}

/// Type `keys` (`⎋` is Esc, `⏎` Enter, `⌫` Backspace) through the app's key handler; returns the text and the drawn caret.
pub(crate) fn type_keys(app: &mut App, keys: &str) -> (String, usize) {
    for c in keys.chars() {
        send_key(
            app,
            key(match c {
                '⎋' => KeyCode::Esc,
                '⏎' => KeyCode::Enter,
                '⌫' => KeyCode::Backspace,
                c => KeyCode::Char(c),
            }),
        );
    }
    (app.chat_input.input.clone(), caret(app))
}

/// Type `keys` in Normal mode from `cursor` in `text`.
pub(crate) fn run(text: &str, cursor: usize, keys: &str) -> (String, usize) {
    type_keys(&mut app(Mode::Normal, text, cursor), keys)
}

#[track_caller]
pub(crate) fn assert_edit(text: &str, cursor: usize, keys: &str, want: (&str, usize)) {
    let (text, cursor) = run(text, cursor, keys);
    assert_eq!((text.as_str(), cursor), want);
}

#[test]
fn word_motions_follow_vim_word_classes() {
    let text = "hello big.world";
    assert_eq!(next_word_start(text, 0), 6);
    assert_eq!(next_word_start(text, 6), 9);
    assert_eq!(next_word_start(text, 10), text.len());
    assert_eq!(prev_word_start(text, 8), 6);
    assert_eq!(prev_word_start(text, 6), 0);
    assert_eq!(word_end(text, 0), 4);
    assert_eq!(word_end(text, 4), 8);
    assert_eq!(word_end(text, text.len()), text.len());
}

#[test]
fn word_motions_stop_on_empty_lines_and_keep_combining_marks() {
    assert_eq!(next_word_start("a\n\nb", 0), 2);
    assert_eq!(next_word_start("a\n\n\nb", 2), 3);
    assert_eq!(prev_word_start("a\n\nb", 3), 2);
    assert_eq!(word_end("a\n\nbc", 0), 4);
    let cafe = "cafe\u{301} x";
    assert_eq!(prev_word_start(cafe, cafe.len() - 1), 0);
    assert_eq!(run(cafe, 0, "dw").0, "x");
    assert_eq!(run("\u{301}ab cd", 0, "cw").0, " cd");
}

#[test]
fn line_motions_stay_on_the_caret_line() {
    assert_eq!(first_non_blank("  hi", 4), 2);
    assert_eq!(last_char("ab\ncd", 0), 1);
    assert_eq!(last_char("ab\n", 3), 3);
}

#[test]
fn escape_enters_normal_on_the_previous_character() {
    let mut app = app(Mode::Insert, "hello", 5);
    assert_eq!(type_keys(&mut app, "⎋"), ("hello".to_owned(), 4));
    assert_eq!(app.vim.mode, Mode::Normal);
}

#[test]
fn escape_in_normal_mode_keeps_the_draft() {
    assert_edit("hello", 4, "⎋⎋⎋", ("hello", 4));
    assert_eq!(
        type_keys(&mut app(Mode::Insert, "hello", 5), "⎋⎋⎋"),
        ("hello".to_owned(), 4)
    );
}

#[test]
fn normal_mode_motions_move_the_caret() {
    assert_eq!(run("hello big world", 0, "w").1, 6);
    assert_eq!(run("hello big world", 0, "$").1, 14);
    assert_eq!(run("hello big world", 14, "0").1, 0);
    assert_eq!(run("hello", 4, "l").1, 4);
    assert_eq!(run("ab\ncd", 3, "h").1, 3);
    let mut ctrl_h = app(Mode::Normal, "abc", 2);
    send_key(&mut ctrl_h, ctrl('h'));
    assert_eq!(
        (ctrl_h.chat_input.input.as_str(), ctrl_h.chat_input.cursor),
        ("abc", 1)
    );
    assert_eq!(run("  ab\ncd", 6, "gg").1, 2);
    assert_eq!(run("ab\n  cd", 0, "G").1, 5);
    assert_eq!(run("hello", 0, "w").1, 4);
    assert_eq!(run("   ", 0, "^").1, 2);
}

#[test]
fn a_caret_left_past_the_end_outside_vim_is_drawn_and_edited_on_the_last_character() {
    let mut normal = app(Mode::Normal, "abc", 3);
    assert_eq!(caret(&normal), 2);
    assert_eq!(type_keys(&mut normal, "x"), ("ab".to_owned(), 1));
    assert_eq!(run("ab\ncd", 2, "x").0, "a\ncd");
}

#[test]
fn k_recalls_one_history_entry_per_press() {
    let mut app = app(Mode::Normal, "", 0);
    for entry in ["one", "two", "three"] {
        app.chat_input.history.add(entry);
    }
    assert_eq!(type_keys(&mut app, "k").0, "three");
    assert_eq!(type_keys(&mut app, "k").0, "two");
    assert_eq!(type_keys(&mut app, "kj").0, "two");
}

#[test]
fn insert_commands_place_the_caret_then_switch_mode() {
    let mut normal = app(Mode::Normal, "  hi", 3);
    assert_eq!(type_keys(&mut normal, "I").1, 2);
    assert_eq!(normal.vim.mode, Mode::Insert);
    assert_eq!(run("hi", 0, "a").1, 1);
    assert_eq!(run("hi", 0, "A").1, 2);
    assert_eq!(run("ab\ncd", 0, "o"), ("ab\n\ncd".to_owned(), 3));
    assert_eq!(run("ab\ncd", 3, "O"), ("ab\n\ncd".to_owned(), 3));
}

#[test]
fn normal_mode_hands_app_keys_to_the_composer() {
    let mut vim = normal();
    let undo = plan(&mut vim, &chat("hi"), key(KeyCode::Char('u')));
    assert_eq!(undo.restore, Some(Action::Undo));
    let down = plan(&mut vim, &chat("hi"), key(KeyCode::Char('j')));
    assert_eq!(down.keys, [key(KeyCode::Down)]);
    let ctrl_a = KeyEvent::new(KeyCode::Char('a'), KeyModifiers::CONTROL);
    assert_eq!(plan(&mut vim, &chat("hi"), ctrl_a).keys, [ctrl_a]);
    assert_eq!(
        plan(&mut vim, &chat("hi"), key(KeyCode::Char('q'))),
        Plan::default()
    );
    assert_eq!(
        plan(&mut vim, &chat("hi"), key(KeyCode::Enter)).keys,
        [key(KeyCode::Enter)]
    );
}

#[tokio::test]
async fn only_a_submitting_enter_returns_to_insert() {
    let mut normal = app(Mode::Normal, "ab", 1);
    send_key(
        &mut normal,
        KeyEvent::new(KeyCode::Enter, KeyModifiers::SHIFT),
    );
    assert_eq!(normal.chat_input.input, "a\nb");
    assert_eq!(normal.vim.mode, Mode::Normal);
    send_key(&mut normal, key(KeyCode::Enter));
    assert_eq!(normal.chat_input.input, "");
    assert_eq!(normal.vim.mode, Mode::Insert);
    let mut empty = app(Mode::Normal, "", 0);
    send_key(&mut empty, key(KeyCode::Enter));
    assert_eq!(empty.vim.mode, Mode::Normal);
    let mut keypad = app(Mode::Normal, "hi", 0);
    let mut enter = key(KeyCode::Enter);
    enter.state = KeyEventState::KEYPAD;
    send_key(&mut keypad, enter);
    assert_eq!(keypad.vim.mode, Mode::Insert);
}

#[test]
fn normal_keys_count_as_typing() {
    let mut normal = app(Mode::Normal, "hello world", 0);
    type_keys(&mut normal, "w");
    assert!(normal.chat_input.last_keystroke.is_some());
}

#[test]
fn slash_on_an_empty_draft_opens_the_command_mode() {
    let mut vim = normal();
    let slash = key(KeyCode::Char('/'));
    assert_eq!(plan(&mut vim, &chat(""), slash).keys, [slash]);
    assert_eq!(vim.mode, Mode::Insert);
    let mut vim = normal();
    assert_eq!(plan(&mut vim, &chat("hi"), slash), Plan::default());
}

#[tokio::test]
async fn escape_cancels_a_pending_operator() {
    let mut normal = app(Mode::Normal, "hello", 0);
    assert_eq!(type_keys(&mut normal, "d⎋x").0, "ello");
    assert_eq!(normal.vim.pending, None);
    assert_eq!(type_keys(&mut normal, "d⏎x").0, "llo");
    assert_eq!(normal.vim.pending, None);
}

#[test]
fn the_config_toggles_vim_mode_and_resets_it_to_insert() {
    let config = |enabled| json!({"runtime": {"config": {"vimKeybindings": enabled}}});
    let mode = |app: &App| app.vim.enabled.then_some(app.vim.mode);
    let mut app = App::default();
    assert_eq!(mode(&app), None);
    apply_runtime_value(&mut app, &config(true));
    assert_eq!(mode(&app), Some(Mode::Insert));
    assert!(inserting(&app));
    app.vim.mode = Mode::Normal;
    apply_runtime_value(&mut app, &config(true));
    assert_eq!(mode(&app), Some(Mode::Normal));
    assert!(!inserting(&app));
    apply_runtime_value(&mut app, &config(false));
    assert_eq!(mode(&app), None);
    assert!(!inserting(&app));
    apply_runtime_value(&mut app, &config(true));
    assert_eq!(mode(&app), Some(Mode::Insert));
    apply_runtime_value(&mut app, &json!({}));
    assert_eq!(mode(&app), None);
}

#[test]
fn digits_rate_feedback_in_normal_mode() {
    let mut normal = app(Mode::Normal, "hi", 0);
    normal.feedback.message = Message::Prompt;
    type_keys(&mut normal, "1");
    assert!(normal.feedback.message == Message::ThankYou);
}

#[test]
fn normal_mode_edits_dismiss_the_feedback_prompt() {
    let mut normal = app(Mode::Normal, "abc def", 0);
    normal.feedback.message = Message::Prompt;
    assert_eq!(type_keys(&mut normal, "wx0"), ("abc ef".to_owned(), 0));
    assert!(normal.feedback.message == Message::Hidden);
}

#[test]
fn a_late_path_mention_keeps_the_normal_caret_on_a_character() {
    let mut normal = app(Mode::Normal, "/x/a.txt", 7);
    let probed = paste_image::Event::Probed {
        start: 0,
        raw: "/x/a.txt".into(),
        paths: Some(vec!["/x/a.txt".into()]),
    };
    paste_image::apply_event(&mut normal, probed);
    let input = &normal.chat_input.input;
    assert!(input.starts_with('@'));
    assert_eq!(caret(&normal), last_char(input, input.len()));
}

#[test]
fn the_first_normal_mode_entry_is_recorded_once() {
    let mut insert = app(Mode::Insert, "hi", 2);
    let (tx, mut rx) = tokio::sync::mpsc::channel(telemetry::TELEMETRY_CHANNEL_CAP);
    insert.telemetry_tx = Some(tx);
    type_keys(&mut insert, "⎋i⎋");
    let names: Vec<_> = std::iter::from_fn(|| rx.try_recv().ok())
        .map(|event| event.name)
        .collect();
    assert_eq!(names, [telemetry::event::VIM_NORMAL_MODE_ENTERED]);
}
