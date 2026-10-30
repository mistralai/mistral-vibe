//! Queue key hints stay in step with what the queue keys actually do.

use std::time::Instant;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use vibe_rs::app::{App, Status};
use vibe_rs::message_queue::{self as mq};

use crate::support::{press, queued, stub};

fn busy_app(ids: &[&str]) -> App {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.active_turn_id = Some("turn".into());
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    app.queue.items = ids.iter().map(|id| queued(id, id)).collect();
    app
}

fn select_key(app: &mut App, code: KeyCode, modifiers: KeyModifiers) {
    mq::handle_selection_key(app, &stub(), KeyEvent::new(code, modifiers));
}

fn keys(app: &App) -> Vec<&'static str> {
    mq::mode_hints(app)
        .unwrap_or_default()
        .iter()
        .map(|(keys, _)| *keys)
        .collect()
}

#[test]
fn selection_hints_last_exactly_as_long_as_the_selection() {
    let mut app = busy_app(&["a", "b"]);
    assert!(mq::mode_hints(&app).is_none());

    assert!(mq::enter(&mut app));
    assert_eq!(keys(&app), ["↑↓/jk", "Enter", "Backspace/Ctrl+C", "Esc"]);

    mq::edit_selected(&mut app);
    assert_eq!(keys(&app), ["Enter", "Esc"]);

    mq::end_edit(&mut app);
    assert_eq!(keys(&app), ["↑↓/jk", "Enter", "Backspace/Ctrl+C", "Esc"]);

    select_key(&mut app, KeyCode::Esc, KeyModifiers::NONE);
    assert!(mq::mode_hints(&app).is_none());
}

#[tokio::test]
async fn a_consumed_edit_hints_submit_as_new() {
    let mut app = busy_app(&["a", "b"]);
    assert!(mq::enter(&mut app));
    mq::edit_selected(&mut app);
    mq::remove_selected(&mut app, &stub());
    let hints = mq::mode_hints(&app).expect("still editing");
    assert_eq!(
        hints[0],
        ("Enter", "submit as new"),
        "Enter no longer saves"
    );
    assert!(mq::confirm_consumed_edit(&mut app));

    let hints = mq::mode_hints(&app).expect("still editing");
    assert_eq!(hints[0], ("Enter", "submit as new"));
}

#[tokio::test]
async fn ctrl_c_and_backspace_remove_the_highlighted_prompt_but_delete_does_not() {
    let mut app = busy_app(&["a", "b", "c"]);
    assert!(mq::enter(&mut app));
    mq::select_older(&mut app);

    select_key(&mut app, KeyCode::Delete, KeyModifiers::NONE);
    assert_eq!(app.queue.len(), 3, "Delete is not a removal key");

    select_key(&mut app, KeyCode::Char('c'), KeyModifiers::CONTROL);
    let left: Vec<_> = app
        .queue
        .items
        .iter()
        .map(|i| i.message_id.as_str())
        .collect();
    assert_eq!(
        left,
        ["a", "c"],
        "the highlighted prompt goes, not the newest"
    );

    select_key(&mut app, KeyCode::Backspace, KeyModifiers::NONE);
    assert_eq!(app.queue.len(), 1);
}

#[tokio::test]
async fn esc_on_a_focused_list_returns_to_the_input_and_keeps_the_edit() {
    let mut app = busy_app(&["a", "b"]);
    assert!(mq::enter(&mut app));
    mq::edit_selected(&mut app);
    app.subagents.list.focused = true;

    press(&mut app, KeyCode::Esc);

    assert!(app.queue.editing, "the edit is kept");
    assert!(!app.subagents.list.focused, "Esc goes back one level");
    assert!(
        matches!(app.session.status, Status::Generating { .. }),
        "the first Esc does not interrupt the turn"
    );
}

#[test]
fn esc_in_a_subagent_view_returns_to_main_and_keeps_the_edit() {
    let mut app = busy_app(&["a", "b"]);
    assert!(mq::enter(&mut app));
    mq::edit_selected(&mut app);
    app.subagents.viewed_subagent_id = Some("child".into());

    press(&mut app, KeyCode::Esc);

    assert!(app.queue.editing, "the edit is kept");
    assert_eq!(app.subagents.viewed_subagent_id, None);
}

#[test]
fn can_steer_only_when_an_empty_enter_would_steer() {
    let mut app = busy_app(&["a"]);
    assert!(mq::can_steer(&app));

    app.queue.paused = true;
    assert!(!mq::can_steer(&app), "Enter resumes a paused queue");
    app.queue.paused = false;

    app.queue.items[0].queue_item_id = None;
    assert!(!mq::can_steer(&app), "not accepted by the server yet");
    app.queue.items[0].queue_item_id = Some("q-a".into());

    assert!(mq::enter(&mut app));
    assert!(!mq::can_steer(&app), "queue selection owns Enter");
    mq::exit(&mut app);

    app.session.status = Status::Starting;
    assert!(!mq::can_steer(&app), "no turn to steer into");
}
