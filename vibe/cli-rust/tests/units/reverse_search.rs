//! Ctrl+R reverse history search: matching, match preview, Up/Down traversal, accept and cancel.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::input_modes::InputMode;
use vibe_rs::reverse_search::{match_ranges, matching, Status, VOICE_MOVED};

use crate::support::{ctrl, press, send_key, type_text};

fn history(entries: &[&str]) -> Vec<String> {
    entries.iter().map(|entry| (*entry).to_owned()).collect()
}

fn app_with(entries: &[&str]) -> App {
    let mut app = App::default();
    for entry in entries {
        app.chat_input.history.add(entry);
    }
    app
}

fn search(app: &mut App, query: &str) {
    send_key(app, ctrl('r'));
    type_text(app, query);
}

fn status(app: &App) -> Option<Status> {
    app.reverse_search.as_ref().map(|search| search.status)
}

fn notice(app: &App) -> Option<&str> {
    app.overlays
        .notice
        .as_ref()
        .map(|notice| notice.text.as_str())
}

#[test]
fn matching_is_a_case_insensitive_substring_newest_first_without_duplicates() {
    let entries = history(&[
        "Build Release",
        "fix build",
        "build release",
        "Build Release",
    ]);
    assert_eq!(
        matching(&entries, "BUILD"),
        ["Build Release", "build release", "fix build"]
    );
    assert!(matching(&entries, "bld").is_empty(), "no fuzzy match");
}

#[test]
fn match_ranges_ignore_case_and_keep_char_boundaries() {
    assert_eq!(match_ranges("Test the tests", "test"), [(0, 4), (9, 13)]);
    assert_eq!(match_ranges("café CAFÉ", "café"), [(0, 5), (6, 11)]);
    assert!(match_ranges("anything", "").is_empty());
}

#[test]
fn ctrl_r_opens_without_previewing_the_latest_entry() {
    let mut app = app_with(&["remembered"]);
    type_text(&mut app, "draft");
    send_key(&mut app, ctrl('r'));
    assert_eq!(status(&app), Some(Status::Idle));
    assert_eq!(app.chat_input.input, "draft");
}

#[test]
fn typing_previews_the_newest_match_in_the_composer() {
    let mut app = app_with(&["git status", "cargo test", "git push"]);
    search(&mut app, "git");
    assert_eq!(status(&app), Some(Status::Match));
    assert_eq!(app.chat_input.input, "git push");
    assert_eq!(app.reverse_search.as_ref().unwrap().query, "git");
}

#[test]
fn up_and_ctrl_r_go_older_down_goes_newer_and_both_stop_at_the_ends() {
    let mut app = app_with(&["test one", "test two", "test three"]);
    search(&mut app, "test");
    press(&mut app, KeyCode::Up);
    assert_eq!(app.chat_input.input, "test two");
    send_key(&mut app, ctrl('r'));
    send_key(&mut app, ctrl('r'));
    assert_eq!(app.chat_input.input, "test one", "the oldest match stays");
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Down);
    press(&mut app, KeyCode::Down);
    assert_eq!(app.chat_input.input, "test three", "the newest match stays");
    assert_eq!(status(&app), Some(Status::Match));
}

#[test]
fn editing_the_query_restarts_from_the_newest_match() {
    let mut app = app_with(&["test alpha", "test beta"]);
    search(&mut app, "test");
    press(&mut app, KeyCode::Up);
    assert_eq!(app.chat_input.input, "test alpha");
    press(&mut app, KeyCode::Backspace);
    assert_eq!(app.chat_input.input, "test beta");
}

#[test]
fn no_match_shows_the_draft_and_keeps_the_search_open() {
    let mut app = app_with(&["git status"]);
    type_text(&mut app, "draft");
    search(&mut app, "zzz");
    assert_eq!(status(&app), Some(Status::NoMatch));
    assert_eq!(app.chat_input.input, "draft");
    press(&mut app, KeyCode::Enter);
    assert!(app.reverse_search.is_some(), "Enter accepts only a match");
}

#[test]
fn clearing_the_query_goes_back_to_idle_and_the_draft() {
    let mut app = app_with(&["git status"]);
    type_text(&mut app, "draft");
    search(&mut app, "git");
    send_key(&mut app, ctrl('u'));
    assert_eq!(status(&app), Some(Status::Idle));
    assert_eq!(app.chat_input.input, "draft");
}

#[test]
fn enter_keeps_the_match_as_an_editable_draft_without_sending_it() {
    let mut app = app_with(&["first line\nsecond line"]);
    search(&mut app, "second");
    press(&mut app, KeyCode::Enter);
    assert!(app.reverse_search.is_none());
    assert_eq!(app.chat_input.input, "first line\nsecond line");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
    assert_eq!(
        app.chat_input.history.entries().len(),
        1,
        "nothing was sent"
    );
    type_text(&mut app, "!");
    assert_eq!(app.chat_input.input, "first line\nsecond line!");
}

#[test]
fn an_accepted_command_reopens_its_mode() {
    let mut app = app_with(&["/model", "hello"]);
    search(&mut app, "mod");
    press(&mut app, KeyCode::Enter);
    assert_eq!(
        (app.chat_input.mode, app.chat_input.input.as_str()),
        (InputMode::Slash, "model")
    );
}

#[test]
fn undo_after_accepting_brings_the_draft_back() {
    let mut app = app_with(&["recalled"]);
    type_text(&mut app, "my draft");
    search(&mut app, "rec");
    press(&mut app, KeyCode::Enter);
    send_key(
        &mut app,
        KeyEvent::new(KeyCode::Char('z'), KeyModifiers::SUPER),
    );
    assert_eq!(app.chat_input.input, "my draft");
}

#[test]
fn escape_restores_the_draft_mode_and_caret() {
    let mut app = app_with(&["ls -la"]);
    type_text(&mut app, "!pwd");
    press(&mut app, KeyCode::Left);
    search(&mut app, "ls");
    assert_eq!(app.chat_input.input, "ls -la");
    press(&mut app, KeyCode::Esc);
    assert!(app.reverse_search.is_none());
    assert_eq!(
        (app.chat_input.mode, app.chat_input.input.as_str()),
        (InputMode::Bash, "pwd")
    );
    assert_eq!(app.chat_input.cursor, "pw".len());
}

#[test]
fn ctrl_c_cancels_the_search_before_anything_else() {
    let mut app = app_with(&["git status"]);
    type_text(&mut app, "draft");
    search(&mut app, "git");
    assert!(!send_key(&mut app, ctrl('c')));
    assert!(app.reverse_search.is_none());
    assert_eq!(app.chat_input.input, "draft");
}

#[test]
fn a_paste_extends_the_query() {
    let mut app = app_with(&["deploy prod"]);
    send_key(&mut app, ctrl('r'));
    vibe_rs::input::handle_paste(&mut app, "deploy p".to_owned());
    assert_eq!(app.reverse_search.as_ref().unwrap().query, "deploy p");
    assert_eq!(app.chat_input.input, "deploy prod");
}

#[test]
fn voice_users_are_told_on_every_ctrl_r_that_recording_moved_to_ctrl_t() {
    let mut app = app_with(&["anything"]);
    app.voice.mode_enabled = true;
    send_key(&mut app, ctrl('r'));
    assert!(app.reverse_search.is_some(), "Ctrl+R still searches");
    assert_eq!(notice(&app), Some(VOICE_MOVED));
    press(&mut app, KeyCode::Esc);
    app.overlays.notice = None;
    send_key(&mut app, ctrl('r'));
    assert_eq!(notice(&app), Some(VOICE_MOVED), "the hint shows every time");
}

#[test]
fn ctrl_t_does_not_search() {
    let mut app = app_with(&["anything"]);
    send_key(&mut app, ctrl('t'));
    assert!(app.reverse_search.is_none());
}

#[test]
fn ctrl_t_in_voice_mode_ends_the_search_and_restores_the_draft() {
    let mut app = app_with(&["git status"]);
    app.voice.mode_enabled = true;
    type_text(&mut app, "draft");
    search(&mut app, "git");
    send_key(&mut app, ctrl('t'));
    assert!(app.reverse_search.is_none());
    assert_eq!(app.chat_input.input, "draft");
}

#[test]
fn ctrl_d_neither_deletes_the_preview_nor_quits() {
    let mut app = app_with(&["git status"]);
    type_text(&mut app, "draft");
    press(&mut app, KeyCode::Home);
    send_key(&mut app, ctrl('r'));
    assert!(!send_key(&mut app, ctrl('d')));
    assert_eq!(app.chat_input.input, "draft");
    assert!(app.quit.active().is_none());
    assert!(app.reverse_search.is_some());
}

#[test]
fn searching_from_a_recalled_entry_restores_the_typed_draft() {
    let mut app = app_with(&["older"]);
    type_text(&mut app, "typed");
    press(&mut app, KeyCode::Home);
    press(&mut app, KeyCode::Up);
    assert_eq!(app.chat_input.input, "older");
    send_key(&mut app, ctrl('r'));
    assert_eq!(
        app.chat_input.input, "typed",
        "an idle search shows the draft"
    );
    press(&mut app, KeyCode::Esc);
    assert_eq!(app.chat_input.input, "typed");
}

#[test]
fn pasted_control_characters_stay_out_of_the_query() {
    let mut app = app_with(&["deploy"]);
    send_key(&mut app, ctrl('r'));
    vibe_rs::input::handle_paste(&mut app, "dep\u{1b}[31mloy".to_owned());
    assert_eq!(app.reverse_search.as_ref().unwrap().query, "dep[31mloy");
}

#[test]
fn opening_a_subagent_view_ends_the_search_and_restores_the_draft() {
    let mut app = app_with(&["git status"]);
    app.subagents.status_list_enabled = true;
    type_text(&mut app, "draft");
    search(&mut app, "git");
    vibe_rs::subagents::show_subagent_chat(&mut app, "child");
    assert!(app.reverse_search.is_none());
    assert_eq!(app.chat_input.input, "draft");
}
