//! The PR item on the rail: its place, `p`, the window it opens, and the sync it runs as it opens.

use crossterm::event::KeyCode;
use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::artifacts;
use vibe_rs::focus::Focus;
use vibe_rs::rail::{self, Item};
use vibe_rs::todo_tracker::{TodoItem, TodoStatus};

use crate::artifacts_actions::{app_with, pr};
use crate::support::{ctrl, press, send_key, stub};

fn with_prs() -> App {
    let mut fresh = pr(2, 11, json!({ "prStatus": "mergeable" }));
    fresh.viewed_at = None;
    let mut app = app_with(vec![pr(1, 10, json!({})), fresh]);
    app.todo_tracker.record(vec![TodoItem {
        id: "1".into(),
        content: "Ship".into(),
        status: TodoStatus::Pending,
    }]);
    app
}

#[test]
fn prs_sit_before_the_todo() {
    assert_eq!(rail::items(&with_prs()), [Item::Prs, Item::Plan]);
}

#[tokio::test]
async fn ctrl_x_p_opens_the_pr_window_and_esc_returns_to_the_rail() {
    let mut app = with_prs();
    send_key(&mut app, ctrl('x'));
    press(&mut app, KeyCode::Char('p'));
    assert_eq!(app.focus(), Focus::RailWindow);
    assert_eq!(rail::open_item(&app), Some(Item::Prs));
    assert!(!app.rail.letters);

    press(&mut app, KeyCode::Esc);
    assert_eq!(app.focus(), Focus::Rail);
}

#[tokio::test]
async fn opening_syncs_the_statuses_and_marks_every_pr_seen() {
    let mut app = with_prs();
    assert!(artifacts::is_unseen(&app, &app.session.artifacts[0]));
    let (client, mut wire) = vibe_rs::server::Client::stub_answering();
    rail::open(&mut app, &std::sync::Arc::new(client), Item::Prs);

    let sync = wire.recv().await.expect("the status sync");
    assert_eq!(sync["method"], "artifacts/sync");
    assert!(app
        .session
        .artifacts
        .iter()
        .all(|artifact| !artifacts::is_unseen(&app, artifact)));
}

#[tokio::test]
async fn arrows_and_ctrl_x_leave_the_pr_window_for_the_rail() {
    let mut app = with_prs();
    rail::open(&mut app, &stub(), Item::Prs);
    press(&mut app, KeyCode::Right);
    assert_eq!(
        app.focus(),
        Focus::RailWindow,
        "Right stays on a rail window"
    );
    assert!(!vibe_rs::artifacts_modal::is_open(&app));
    assert_eq!(rail::open_item(&app), Some(Item::Plan));

    press(&mut app, KeyCode::Right);
    assert_eq!(rail::open_item(&app), Some(Item::Prs), "Right wraps");
    send_key(&mut app, ctrl('x'));
    assert_eq!(app.focus(), Focus::Rail);
    assert!(app.rail.letters, "Ctrl+X always shows the letters");
    assert_eq!(rail::selected(&app), Some(Item::Prs));
}
