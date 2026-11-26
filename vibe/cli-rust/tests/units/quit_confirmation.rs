//! Ctrl+C / Ctrl+D double-press quit confirmation and its bottom-bar hint.

use vibe_rs::app::App;
use vibe_rs::input::handle_priority_key;
use vibe_rs::message_queue::QueueItem;
use vibe_rs::quit_manager::QuitConfirmKey;

use crate::support::{bottom_bar_row, ctrl, queued, stub};

fn press(app: &mut App, key: char) -> Option<bool> {
    let event = ctrl(key);
    handle_priority_key(app, &stub(), event)
}

#[test]
fn only_the_arming_key_confirms_quit() {
    let mut app = App::default();
    assert_eq!(press(&mut app, 'c'), Some(false));
    assert_eq!(press(&mut app, 'd'), Some(false));
    assert!(app.quit.is_confirmed(QuitConfirmKey::CtrlD));
    assert_eq!(press(&mut app, 'd'), Some(true));
}

#[test]
fn ctrl_d_with_queued_prompts_warns_they_will_be_discarded() {
    let mut app = App::default();
    for id in ["q1", "q2"] {
        app.queue.items.push(QueueItem {
            queue_item_id: Some(id.into()),
            revision: 1,
            ..queued(id, "queued")
        });
    }
    assert_eq!(press(&mut app, 'd'), Some(false));
    assert_eq!(app.queue.len(), 2, "arming the quit keeps the queue");
    let row = bottom_bar_row(&mut app, 120);
    assert!(row.starts_with("Press Ctrl+D again to quit (2 queued messages will be discarded)"));
}

#[test]
fn ctrl_d_quits_at_once_when_confirmation_is_off_and_keeps_the_hidden_draft() {
    let mut app = App::default();
    app.quit.ask_confirmation_on_exit = false;
    app.subagents.viewed_subagent_id = Some("child".into());
    app.chat_input.input = "draft".into();
    app.chat_input.cursor = 0;
    assert_eq!(press(&mut app, 'd'), Some(true));
    assert_eq!(app.chat_input.input, "draft");
}
