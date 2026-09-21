//! Prompt-queue requests and the ADR-0013 selection state machine.

use std::sync::Arc;
use std::time::Instant;

use serde_json::json;
use vibe_rs::app::{App, Status};
use vibe_rs::message_queue::{self as mq, QueueController, QueueItem};
use vibe_rs::server::Client;

fn generating_app() -> App {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    app
}

fn item(id: &str, text: &str, queue_item_id: Option<&str>) -> QueueItem {
    QueueItem {
        queue_item_id: queue_item_id.map(str::to_owned),
        message_id: id.into(),
        server_message_id: id.into(),
        text: text.into(),
        images: Vec::new(),
        sent: queue_item_id.is_some(),
        ever_sent: queue_item_id.is_some(),
        revision: 0,
        replacing: false,
    }
}

#[tokio::test]
async fn enqueue_when_busy_mounts_a_pending_prompt() {
    let mut app = generating_app();
    let client = Arc::new(Client::stub());
    mq::enqueue_prompt(&mut app, &client, "run tests".into());

    assert_eq!(app.queue.len(), 1);
    assert_eq!(app.queue.items[0].text, "run tests");
    assert!(app.queue.items[0].sent);
    assert!(app.view.transcript.entry(0).expect("mounted").pending);
}

#[tokio::test]
async fn busy_time_prompts_merge_into_the_first_server_group() {
    let mut app = generating_app();
    let client = Arc::new(Client::stub());
    mq::enqueue_prompt(&mut app, &client, "one".into());
    mq::enqueue_prompt(&mut app, &client, "two".into());

    let first = app.queue.items[0].server_message_id.clone();
    assert_eq!(app.queue.items[1].server_message_id, first);
    assert!(!app.queue.items[1].sent);
    assert_ne!(app.queue.items[1].message_id, app.queue.items[0].message_id);
}

#[tokio::test]
async fn enqueue_when_ready_starts_optimistically() {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.status = Status::Ready;
    let client = Arc::new(Client::stub());
    mq::enqueue_prompt(&mut app, &client, "hello".into());

    let entry = app.view.transcript.entry(0).expect("mounted");
    assert!(!entry.pending);
    assert!(matches!(app.session.status, Status::Generating { .. }));
    assert!(app.queue.items[0].sent);
}

#[tokio::test]
async fn flush_pending_sends_prompts_held_during_startup() {
    let mut app = App::default();
    app.session.session_id = Some("sess".into());
    app.session.status = Status::Starting;
    let client = Arc::new(Client::stub());
    mq::enqueue_prompt(&mut app, &client, "held".into());
    assert!(!app.queue.items[0].sent, "dispatch holds on Starting");

    app.session.status = Status::Ready;
    mq::flush_pending(&mut app, &client);
    assert!(app.queue.items[0].sent);
}

#[test]
fn pop_last_removes_the_newest_prompt() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None), item("b", "second", None)];

    assert!(mq::pop_last(&mut app, &Arc::new(Client::stub())));
    assert_eq!(app.queue.len(), 1);
    assert_eq!(app.queue.items[0].message_id, "a");
    assert!(app.view.transcript.is_empty(), "b left the transcript");

    assert!(
        mq::pop_last(&mut app, &Arc::new(Client::stub())),
        "a pops too"
    );
    assert!(!mq::pop_last(&mut app, &Arc::new(Client::stub())));
    assert!(app.queue.is_empty());
}

#[test]
fn remove_selected_drops_the_highlighted_prompt() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None), item("b", "second", None)];
    app.queue.selected = Some("b".into());

    mq::remove_selected(&mut app, &Arc::new(Client::stub()));
    assert_eq!(app.queue.len(), 1);
    assert_eq!(app.queue.items[0].message_id, "a");
}

#[test]
fn replace_selected_rewrites_an_unsent_prompt_in_place() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None)];
    let mut unsent = item("b", "second", None);
    unsent.sent = false;
    app.queue.items.push(unsent);
    app.queue.selected = Some("b".into());

    mq::replace_selected(&mut app, &Arc::new(Client::stub()), "edited".into());
    assert_eq!(app.queue.len(), 2, "no new prompt is created");
    assert_eq!(app.queue.items[1].text, "edited");
    assert_eq!(app.queue.items[1].message_id, "b");
}

#[test]
fn replace_selected_copies_when_the_prompt_already_left() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None)];
    app.queue.selected = Some("consumed".into());

    mq::replace_selected(&mut app, &Arc::new(Client::stub()), "edited".into());
    assert_eq!(app.queue.len(), 2, "the edit is copied into a new prompt");
    assert_eq!(app.queue.items[0].text, "first");
    assert_eq!(app.queue.items[1].text, "edited");
}

#[tokio::test]
async fn resume_releases_a_paused_queue_once() {
    let mut app = generating_app();
    app.queue.paused = true;
    let client = Arc::new(Client::stub());

    mq::resume(&mut app, &client);
    assert!(!app.queue.paused);

    app.queue.paused = false;
    mq::resume(&mut app, &client);
    assert!(!app.queue.paused);
}

#[test]
fn is_available_gates_on_status_and_a_non_empty_queue() {
    let mut app = App::default();
    app.session.status = Status::Ready;
    app.queue.items = vec![item("a", "first", None)];
    assert!(!mq::is_available(&app), "Ready never opens queue mode");
    app.session.status = Status::Generating {
        since: Instant::now(),
    };
    assert!(mq::is_available(&app));
    app.queue.clear();
    assert!(!mq::is_available(&app), "no prompts, no queue mode");

    app.queue.items = vec![item("a", "first", None)];
    app.session.status = Status::Starting;
    assert!(mq::is_available(&app));
}

#[test]
fn enter_selects_the_newest_prompt_and_saves_the_draft() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None), item("b", "second", None)];
    app.chat_input.input = "draft text".into();

    assert!(mq::enter(&mut app));
    assert_eq!(app.queue.selected.as_deref(), Some("b"));
    assert_eq!(app.queue.draft, "draft text");
    assert!(!app.queue.editing);

    assert!(!mq::enter(&mut app), "already open");
    assert!(app.overlays.notice.is_some(), "controls hint appears");
}

#[test]
fn selection_moves_and_clamps_at_the_boundaries() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None), item("b", "second", None)];
    app.chat_input.input = "draft text".into();
    assert!(mq::enter(&mut app));

    mq::select_older(&mut app);
    assert_eq!(app.queue.selected_position(), Some(0));
    mq::select_older(&mut app);
    assert_eq!(
        app.queue.selected_position(),
        Some(0),
        "clamped at the head"
    );

    mq::select_newer(&mut app);
    assert_eq!(app.queue.selected_position(), Some(1));
    mq::select_newer(&mut app);
    assert_eq!(
        app.queue.selected, None,
        "past the newest, queue mode exits"
    );
    assert_eq!(app.chat_input.input, "draft text");
}

#[test]
fn edit_selected_loads_the_text_and_end_edit_clears_it() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "the queued text", None)];
    assert!(mq::enter(&mut app));

    mq::edit_selected(&mut app);
    assert!(app.queue.editing);
    assert_eq!(app.chat_input.input, "the queued text");
    assert_eq!(app.chat_input.cursor, app.chat_input.input.len());
    assert!(app.queue.selected.is_some(), "still selected while editing");

    mq::end_edit(&mut app);
    assert!(!app.queue.editing);
    assert!(app.chat_input.input.is_empty());
}

#[test]
fn exit_restores_the_draft_after_plain_selection() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None)];
    app.chat_input.input = "draft text".into();
    assert!(mq::enter(&mut app));

    mq::exit(&mut app);
    assert_eq!(app.queue.selected, None);
    assert!(!app.queue.editing);
    assert_eq!(app.chat_input.input, "draft text");
    assert_eq!(app.chat_input.cursor, 0);
}

#[test]
fn exit_after_editing_clears_the_input() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None)];
    app.chat_input.input = "draft text".into();
    assert!(mq::enter(&mut app));
    mq::edit_selected(&mut app);

    mq::exit(&mut app);
    assert_eq!(app.queue.selected, None);
    assert!(app.chat_input.input.is_empty());
    assert!(app.overlays.notice.is_none(), "the edit hint is cleared");
}

#[test]
fn turn_started_drops_promoted_prompts() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", Some("q1"))];
    let client = Arc::new(Client::stub());

    assert!(mq::turn_started(&mut app, &client, "q1"));
    assert!(app.queue.is_empty());

    assert!(
        !mq::turn_started(&mut app, &client, "unknown"),
        "not queued yet"
    );
}

#[test]
fn sync_adopts_queued_turns_from_the_server_once() {
    let mut app = generating_app();
    let queue = json!({
        "paused": true,
        "items": [{
            "id": "q9",
            "entries": [{
                "role": "user",
                "entryId": "m9",
                "content": [{"type": "text", "text": "resumed prompt"}]
            }]
        }]
    });
    mq::sync(&mut app, &queue);
    mq::sync(&mut app, &queue);

    assert!(app.queue.paused);
    assert_eq!(app.queue.len(), 1);
    assert_eq!(app.queue.items[0].queue_item_id.as_deref(), Some("q9"));
    assert!(app.queue.items[0].sent);
    assert_eq!(app.view.transcript.entry(0).expect("mounted").id, "m9");
}

#[test]
fn clear_forgets_every_queued_prompt() {
    let mut app = generating_app();
    app.queue.items = vec![item("a", "first", None)];
    app.queue.selected = Some("a".into());
    app.queue.draft = "draft".into();
    app.queue.paused = true;

    app.queue.clear();
    assert!(app.queue.is_empty());
    assert_eq!(app.queue.selected, None);
    assert!(app.queue.draft.is_empty());
    assert!(!app.queue.paused);
}

#[test]
fn queue_positions_resolve_by_message_id() {
    let mut controller = QueueController::default();
    controller.items = vec![item("a", "first", None), item("b", "second", None)];
    assert_eq!(controller.len(), 2);
    assert_eq!(controller.position("b"), Some(1));
    assert_eq!(controller.position("missing"), None);
    controller.selected = Some("missing".into());
    assert_eq!(controller.selected_position(), None);
}
