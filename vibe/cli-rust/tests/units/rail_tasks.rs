//! The background tasks window: a failed stop says so, a task without an id never lists, and a window whose item left stays closed.

use crossterm::event::KeyCode;
use serde_json::json;
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::commands::CommandEvent;
use vibe_rs::rail::{self, Item};
use vibe_rs::server::PublicBackgroundProcess;

use crate::rail::with_tasks;
use crate::support::{press, stub};

#[tokio::test]
async fn a_failed_stop_reports_the_task() {
    let mut app = App::default();
    app.session.session_id = Some("session-1".into());
    let (tx, mut rx) = mpsc::channel(4);
    app.command_tx = Some(tx);
    with_tasks(&mut app, &["npm run dev"]);
    rail::open(&mut app, &stub(), Item::Tasks);

    press(&mut app, KeyCode::Char('s'));

    assert!(app.session.background_tasks.is_empty());
    let Some(CommandEvent::Error(text)) = rx.recv().await else {
        panic!("expected the failure row");
    };
    assert!(text.starts_with("Failed to stop npm run dev: "), "{text}");
}

#[test]
fn a_task_without_a_process_id_is_never_listed() {
    let task = json!({ "status": "running", "command": "watch" });
    assert!(serde_json::from_value::<PublicBackgroundProcess>(task).is_err());
}

#[test]
fn a_window_whose_item_left_stays_closed_when_it_returns() {
    let mut app = App::default();
    with_tasks(&mut app, &["watch"]);
    rail::open(&mut app, &stub(), Item::Tasks);

    app.session.background_tasks.clear();
    rail::prune(&mut app);
    with_tasks(&mut app, &["watch"]);

    assert_eq!(rail::window(&app), None);
}
