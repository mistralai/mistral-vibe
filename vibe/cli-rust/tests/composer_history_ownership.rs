//! Draft restoration preserves local history; other text fields ignore composer undo.

use std::sync::Arc;
use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::{App, Status};
use vibe_rs::commands::submission;
use vibe_rs::config::ConfigField;
use vibe_rs::input_modes::InputMode;
use vibe_rs::message_queue::{self, QueueItem};
use vibe_rs::server::Client;
use vibe_rs::{chat_input::Action, config_edit, input, keymap, mcp};

fn queued_app() -> App {
    let mut app = App::default();
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    app.queue.items.push(QueueItem {
        queue_item_id: None,
        message_id: "queued".into(),
        server_message_id: "queued".into(),
        text: "queued prompt".into(),
        images: Vec::new(),
        mentions: None,
        sent: false,
        ever_sent: false,
        revision: 0,
        replacing: false,
    });
    app
}

/// Paste a prompt draft; a mode character pasted before its text stays literal.
fn paste_draft(app: &mut App, text: &str) {
    let body = text.trim_start_matches(['/', '!']);
    input::handle_paste(app, body.into());
    app.chat_input.cursor = 0;
    if body.len() < text.len() {
        input::handle_paste(app, text[..1].into());
    }
    app.chat_input.cursor = app.chat_input.input.len();
}

fn undo_all(app: &mut App) {
    while app.chat_input.restore_edit(false) {}
}

#[test]
fn browsing_queue_preserves_draft_undo_and_redo() {
    for text in ["draft", "/literal", "!literal"] {
        let mut app = queued_app();
        paste_draft(&mut app, text);
        input::handle_paste(&mut app, " suffix".into());
        assert!(app.chat_input.restore_edit(false));
        assert!(message_queue::enter(&mut app));

        message_queue::exit(&mut app);

        assert_eq!(app.chat_input.mode, InputMode::Prompt);
        assert_eq!(app.chat_input.input, text);
        assert!(app.chat_input.restore_edit(true));
        assert_eq!(app.chat_input.input, format!("{text} suffix"));
        undo_all(&mut app);
        assert!(app.chat_input.input.is_empty());
    }
}

#[test]
fn browsing_queue_preserves_redo_from_an_empty_draft() {
    let mut app = queued_app();
    input::handle_paste(&mut app, "draft".into());
    assert!(app.chat_input.restore_edit(false));
    assert!(message_queue::enter(&mut app));
    message_queue::exit(&mut app);
    assert!(app.chat_input.restore_edit(true));
    assert_eq!(app.chat_input.input, "draft");
}

#[test]
fn loading_a_queued_prompt_starts_a_separate_history() {
    let mut app = queued_app();
    input::handle_paste(&mut app, "draft".into());
    assert!(message_queue::enter(&mut app));
    message_queue::edit_selected(&mut app);
    assert!(!app.chat_input.restore_edit(false));
    input::handle_paste(&mut app, " edit".into());
    assert!(app.chat_input.restore_edit(false));
    assert_eq!(app.chat_input.input, "queued prompt");
}

#[test]
fn editing_a_queued_slash_message_keeps_it_a_prompt() {
    let mut app = queued_app();
    app.queue.items[0].text = "/config".into();
    assert!(message_queue::enter(&mut app));

    message_queue::edit_selected(&mut app);

    assert_eq!(app.chat_input.mode, InputMode::Prompt);
    assert_eq!(app.chat_input.input, "/config");
}

#[tokio::test]
async fn pending_commands_preserve_draft_history_and_literal_prefixes() {
    for text in ["draft", "/literal", "!literal"] {
        let mut app = App::default();
        app.session.status = Status::Ready;
        paste_draft(&mut app, text);
        input::handle_paste(&mut app, " suffix".into());
        assert!(app.chat_input.restore_edit(false));
        app.pending_commands.push("/help".into());
        let (tx, _rx) = mpsc::channel(1);

        assert!(!submission::flush_pending(
            &mut app,
            &Arc::new(Client::stub()),
            &tx
        ));

        assert_eq!(app.chat_input.mode, InputMode::Prompt);
        assert_eq!(app.chat_input.input, text);
        assert!(app.chat_input.restore_edit(true));
        assert_eq!(app.chat_input.input, format!("{text} suffix"));
        undo_all(&mut app);
        assert!(app.chat_input.input.is_empty());
    }
}

#[tokio::test]
async fn restoring_a_rejected_command_does_not_inherit_draft_history() {
    let mut app = queued_app();
    input::handle_paste(&mut app, "old draft".into());
    assert!(app.chat_input.restore_edit(false));
    app.pending_commands = vec!["/clear".into(), "/help".into()];
    let (tx, _rx) = mpsc::channel(1);

    assert!(!submission::flush_pending(
        &mut app,
        &Arc::new(Client::stub()),
        &tx
    ));

    assert_eq!(app.chat_input.full_text(), "/clear");
    assert!(!app.chat_input.restore_edit(true));
    assert!(!app.chat_input.restore_edit(false));
}

#[test]
fn composer_history_actions_are_not_shared_buffer_edits() {
    for (ch, action) in [('z', Action::Undo), ('y', Action::Redo)] {
        let mapped = keymap::action_for(&KeyEvent::new(KeyCode::Char(ch), KeyModifiers::SUPER));
        assert_eq!(mapped, Some(action.clone()));
        assert!(!action.is_edit());
    }
}

#[test]
fn history_shortcuts_do_not_clear_a_config_validation_error() {
    let mut app = App::default();
    config_edit::open(
        &mut app,
        ConfigField {
            name: "integer".into(),
            path: "/integer".into(),
            kind: "int".into(),
            raw_value: json!("invalid"),
            popular: false,
            writable: true,
            overridden: false,
            enum_choices: Vec::new(),
            description: String::new(),
            value_labels: Default::default(),
            layers: Vec::new(),
        },
    );
    let client = Arc::new(Client::stub());
    let (tx, _rx) = mpsc::channel(1);
    config_edit::handle_key(
        &mut app,
        &client,
        &tx,
        KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
    );
    for ch in ['z', 'y'] {
        config_edit::handle_key(
            &mut app,
            &client,
            &tx,
            KeyEvent::new(KeyCode::Char(ch), KeyModifiers::SUPER),
        );
        let edit = app.config_screen.edit.as_ref().unwrap();
        assert_eq!(edit.draft, "invalid");
        assert_eq!(edit.error.as_deref(), Some("Expected an integer"));
    }
}

#[test]
fn history_shortcuts_do_not_reset_mcp_search_navigation() {
    let mut app = App::default();
    app.mcp.search.focused = true;
    app.mcp.search.query = "search".into();
    app.mcp.selected = 2;
    app.mcp.scroll = 1;
    for ch in ['z', 'y'] {
        assert!(mcp::search::handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char(ch), KeyModifiers::SUPER)
        ));
        assert_eq!(app.mcp.search.query, "search");
        assert_eq!(app.mcp.selected, 2);
        assert_eq!(app.mcp.scroll, 1);
    }
}
