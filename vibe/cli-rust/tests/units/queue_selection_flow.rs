//! Queue selection and edit mode driven by real keys while a turn runs (ADR 0013).

use std::time::Instant;

use crossterm::event::KeyCode;
use ratatui::layout::Rect;
use vibe_rs::app::{App, Status};
use vibe_rs::message_queue::{self as mq};

use crate::support::{press, queued, queued_texts, stub, type_text};

fn busy_app(texts: &[&str]) -> App {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.active_turn_id = Some("turn".into());
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    app.queue.items = texts.iter().map(|text| queued(text, text)).collect();
    // The renderer records the composer area; Up reads its width to find the first row.
    app.view.input_area = Rect::new(0, 0, 80, 3);
    app
}

/// Over a typed draft the first Up parks the caret at its start; the second opens queue mode.
fn open_queue_mode_over_draft(app: &mut App) {
    press(app, KeyCode::Up);
    assert!(app.queue.selected.is_none(), "the draft is protected first");
    press(app, KeyCode::Up);
}

#[tokio::test]
async fn escape_while_editing_discards_the_edit_and_returns_to_a_locked_selection() {
    let mut app = busy_app(&["edit me"]);
    press(&mut app, KeyCode::Up);
    press(&mut app, KeyCode::Enter);
    assert!(app.queue.editing);
    type_text(&mut app, " more");
    assert_eq!(app.chat_input.full_text(), "edit me more");

    press(&mut app, KeyCode::Esc);

    assert!(!app.queue.editing, "Escape leaves edit mode");
    assert_eq!(
        app.queue.selected.as_deref(),
        Some("edit me"),
        "still selected"
    );
    assert_eq!(queued_texts(&app), ["edit me"], "the edit is discarded");
    let locked = app.chat_input.full_text();
    type_text(&mut app, "x");
    assert_eq!(
        app.chat_input.full_text(),
        locked,
        "the input is read-only again"
    );
    assert!(app.queue.selected.is_some());
}

#[tokio::test]
async fn an_edit_keeps_its_target_when_an_older_prompt_leaves_the_queue() {
    let mut app = busy_app(&["oldest", "target", "newest"]);
    press(&mut app, KeyCode::Up);
    press(&mut app, KeyCode::Up);
    press(&mut app, KeyCode::Enter);
    assert_eq!(app.chat_input.full_text(), "target");

    assert!(mq::turn_started(&mut app, &stub(), "q-oldest"));
    assert_eq!(queued_texts(&app), ["target", "newest"]);

    type_text(&mut app, " edited");
    press(&mut app, KeyCode::Enter);

    assert_eq!(queued_texts(&app), ["target edited", "newest"]);
    assert_eq!(app.queue.selected, None, "saving leaves queue mode");
}

#[tokio::test]
async fn removing_the_last_queued_prompt_exits_selection_mode() {
    let mut app = busy_app(&["only item"]);
    app.chat_input.load_full_text("draft".to_owned());
    open_queue_mode_over_draft(&mut app);
    assert_eq!(app.queue.selected.as_deref(), Some("only item"));

    press(&mut app, KeyCode::Backspace);

    assert!(app.queue.is_empty());
    assert_eq!(app.queue.selected, None);
    assert!(
        mq::mode_hints(&app).is_none(),
        "the selection hints go away"
    );
    assert_eq!(app.chat_input.full_text(), "draft");
}

#[test]
fn selection_mode_locks_the_input() {
    let mut app = busy_app(&["queued"]);
    app.chat_input.load_full_text("draft".to_owned());

    open_queue_mode_over_draft(&mut app);
    assert!(app.queue.selected.is_some());

    type_text(&mut app, "hello world");
    assert_eq!(
        app.chat_input.full_text(),
        "draft",
        "typing changes nothing"
    );
    assert_eq!(queued_texts(&app), ["queued"]);

    press(&mut app, KeyCode::Esc);
    assert_eq!(app.queue.selected, None);
}

#[tokio::test]
async fn selection_exits_once_the_queue_drains_through_promotion() {
    let mut app = busy_app(&["queued"]);
    app.chat_input.load_full_text("draft".to_owned());
    open_queue_mode_over_draft(&mut app);
    assert!(app.queue.selected.is_some());

    assert!(mq::turn_started(&mut app, &stub(), "q-queued"));

    assert!(app.queue.is_empty());
    assert_eq!(
        app.queue.selected, None,
        "no stale selection on an empty queue"
    );
    assert!(mq::mode_hints(&app).is_none());
    assert_eq!(app.chat_input.full_text(), "draft", "the draft comes back");
}
