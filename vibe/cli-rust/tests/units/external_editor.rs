//! Ctrl+G external editor: request gating, editor resolution, and loading the saved text.

use std::path::Path;
use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::App;
use vibe_rs::config;
use vibe_rs::external_editor::{apply, command, edited_text, get_editor};
use vibe_rs::input::{handle_key, handle_paste};
use vibe_rs::input_modes::InputMode;
use vibe_rs::long_paste::placeholder;
use vibe_rs::server::Client;

fn press_ctrl_g(app: &mut App) {
    let (config_tx, _config_rx) = tokio::sync::mpsc::channel::<config::Loaded>(1);
    let key = KeyEvent::new(KeyCode::Char('g'), KeyModifiers::CONTROL);
    handle_key(app, &Arc::new(Client::stub()), &config_tx, key);
}

fn long_paste() -> String {
    (0..12)
        .map(|number| format!("row {number}"))
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn ctrl_g_requests_the_editor_without_touching_the_draft() {
    let mut app = App::default();
    app.chat_input.input = "keep my draft".into();
    app.chat_input.cursor = 4;

    press_ctrl_g(&mut app);

    assert!(app.external_editor_requested);
    assert_eq!(app.chat_input.input, "keep my draft");
}

#[test]
fn ctrl_g_is_ignored_while_the_composer_is_hidden() {
    let mut app = App::default();
    app.subagents.viewed_subagent_id = Some("child".into());

    press_ctrl_g(&mut app);

    assert!(!app.external_editor_requested);
}

#[test]
fn visual_wins_over_editor_and_blank_values_are_unset() {
    let some = |value: &str| Some(value.to_owned());
    assert_eq!(get_editor(some("code --wait"), some("vim")), "code --wait");
    assert_eq!(get_editor(some("  "), some("vim")), "vim");
    assert_eq!(get_editor(None, some("")), "nano");
}

#[test]
fn the_editor_command_is_split_like_a_shell_then_gets_the_file() {
    let argv = command("code --wait -n 'my profile'", Path::new("/tmp/vibe_1.md"));

    assert_eq!(
        argv,
        ["code", "--wait", "-n", "my profile", "/tmp/vibe_1.md"]
    );
}

#[test]
fn only_a_real_change_replaces_the_draft() {
    assert_eq!(edited_text("draft\n", "draft\n\n"), None);
    assert_eq!(
        edited_text("draft", "draft edited\n"),
        Some("draft edited".into())
    );
    assert_eq!(edited_text("draft", ""), Some(String::new()));
}

#[test]
fn windows_line_ends_and_bom_are_not_a_change() {
    assert_eq!(edited_text("a\nb", "\u{feff}a\r\nb\r\n"), None);
    assert_eq!(edited_text("a", "a\r\nb\rc"), Some("a\nb\nc".into()));
}

#[test]
fn applying_the_edit_restores_the_mode_and_is_one_undo_step() {
    let mut app = App::default();
    app.chat_input.input = "draft".into();
    app.chat_input.cursor = 5;

    apply(&mut app, "!ls -la\nsecond".into());

    assert_eq!(app.chat_input.mode, InputMode::Bash);
    assert_eq!(app.chat_input.input, "ls -la\nsecond");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
    assert!(app.chat_input.restore_edit(false));
    assert_eq!(app.chat_input.full_text(), "draft");
}

#[test]
fn an_untouched_paste_collapses_again_after_the_edit() {
    let mut app = App::default();
    let paste = long_paste();
    handle_paste(&mut app, paste.clone());
    let expanded = app.chat_input.submitted_text();
    assert_eq!(expanded, paste);

    apply(&mut app, format!("summarize {expanded}"));

    assert_eq!(
        app.chat_input.input,
        format!("summarize {}", placeholder(&paste))
    );
    assert_eq!(
        app.chat_input.submitted_text(),
        format!("summarize {paste}")
    );
}
