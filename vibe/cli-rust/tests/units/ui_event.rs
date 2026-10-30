//! The shared answer channel releases each sender's commit in one place.

use std::sync::atomic::Ordering;

use vibe_rs::app::App;
use vibe_rs::{debug_console, model_picker, ui_event};

use crate::support::stub;

fn pending(app: &App) -> u32 {
    app.pending_commits.load(Ordering::Relaxed)
}

#[test]
fn a_widget_answer_releases_the_commit_its_sender_claimed() {
    let mut app = App::default();
    app.commit_started();
    let event = model_picker::Event::Failed("boom".into());
    ui_event::apply(&mut app, &stub(), event.into());
    assert_eq!(pending(&app), 0);
}

#[test]
fn a_debug_console_poll_leaves_claimed_commits_alone() {
    let mut app = App::default();
    app.commit_started();
    let event = debug_console::Event::Polled(0, vec![]);
    ui_event::apply(&mut app, &stub(), event.into());
    assert_eq!(pending(&app), 1);
}
