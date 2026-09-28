//! The what's-new body is dropped once, on the first submit.

use serde_json::json;
use vibe_rs::app::App;
use vibe_rs::startup::banners;

fn mount_whats_new(app: &mut App, id: &str) {
    app.view.transcript.add(&json!({
        "entry": {
            "id": id,
            "type": "message",
            "role": "whats_new",
            "content": [{"type": "text", "text": "body"}],
            "generationStatus": "completed",
            "local": true,
            "pinnedBottom": true,
            "afterHistory": false,
        }
    }));
    app.session.whats_new_id = Some(id.to_owned());
}

#[test]
fn dismiss_removes_the_mounted_body() {
    let mut app = App::default();
    mount_whats_new(&mut app, "whats-new");

    banners::dismiss_whats_new(&mut app);

    assert!(!app.view.transcript.contains("whats-new"));
    assert!(app.session.whats_new_id.is_none());
}

#[test]
fn dismiss_runs_once_and_leaves_other_rows_alone() {
    let mut app = App::default();
    mount_whats_new(&mut app, "whats-new");
    app.view.transcript.add(&json!({
        "entry": {
            "id": "assistant",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "generationStatus": "completed",
        }
    }));

    banners::dismiss_whats_new(&mut app);
    banners::dismiss_whats_new(&mut app);

    assert!(!app.view.transcript.contains("whats-new"));
    assert!(app.view.transcript.contains("assistant"));
}
