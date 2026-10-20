//! Leaving the resume picker without resuming lands on a usable composer.

use crossterm::event::KeyCode;
use vibe_rs::app::Status;
use vibe_rs::message_queue as mq;
use vibe_rs::resume_picker::{self, Event};

use crate::resume_picker_outcomes::{picking, preview, row, texts, SAVED};
use crate::teleport_support::stub;

#[tokio::test]
async fn an_empty_session_list_closes_the_picker_onto_a_usable_composer() {
    let mut app = picking();
    resume_picker::apply_event(&mut app, &stub(), Event::Loaded(Vec::new()));
    assert!(!app.resume_picker.open);
    assert_eq!(
        texts(&app).last(),
        Some(&row(
            "command_result",
            "No sessions found for this directory."
        ))
    );
    mq::enqueue_prompt(&mut app, &stub(), "next".into());
    assert!(app.queue.items[0].sent);
    assert!(matches!(app.session.status, Status::Generating { .. }));
}

#[tokio::test]
async fn deleting_the_last_session_drops_the_startup_prompt_and_leaves_a_usable_composer() {
    let mut app = picking();
    app.session.initial_prompt = Some("startup prompt".into());
    resume_picker::apply_event(
        &mut app,
        &stub(),
        Event::Deleted {
            id: SAVED.into(),
            error: None,
        },
    );
    assert!(!app.resume_picker.open);
    assert_eq!(app.session.initial_prompt, None);
    mq::enqueue_prompt(&mut app, &stub(), "next".into());
    assert!(app.queue.items[0].sent);
}

#[tokio::test]
async fn leaving_the_picker_restores_the_transcript_and_drops_the_startup_prompt() {
    let mut app = picking();
    app.session.initial_prompt = Some("continue the work".into());
    preview(&mut app);
    resume_picker::handle_key(&mut app, &stub(), KeyCode::Esc.into());
    assert!(!app.resume_picker.open);
    assert_eq!(app.session.initial_prompt, None);
    assert_eq!(
        texts(&app),
        [
            row("user", "Current question"),
            row("command_result", "Resume cancelled.")
        ]
    );
    assert!(app.queue.is_empty());
}
