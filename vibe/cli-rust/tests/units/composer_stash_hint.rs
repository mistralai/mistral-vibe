//! The composer's bottom border offers `Ctrl+S restore stash` only while Ctrl+S can restore it:
//! reverse search, recording, the queue and the subagent views each take the key first.

use vibe_rs::app::{App, Status};
use vibe_rs::voice::TranscribeState;

use crate::support::{ctrl, redraw, rows, send_key, type_text};

/// A ready session holding a stashed draft.
fn stashed() -> App {
    let mut app = App::default();
    app.set_status(Status::Ready);
    type_text(&mut app, "long draft");
    send_key(&mut app, ctrl('s'));
    assert!(app.chat_input.stash.is_some());
    app
}

fn shows_hint(app: &mut App) -> bool {
    rows(&redraw(app))
        .iter()
        .any(|row| row.contains("restore stash"))
}

#[test]
fn a_stash_shows_the_restore_hint() {
    assert!(shows_hint(&mut stashed()));
}

#[test]
fn a_selected_queued_prompt_hides_the_hint() {
    let mut app = stashed();
    app.queue.selected = Some("message-1".to_string());
    assert!(!shows_hint(&mut app));
}

#[test]
fn editing_a_queued_prompt_hides_the_hint() {
    let mut app = stashed();
    app.queue.selected = Some("message-1".to_string());
    app.queue.editing = true;
    assert!(!shows_hint(&mut app));
}

#[test]
fn reverse_search_hides_the_hint() {
    let mut app = stashed();
    app.chat_input.history.add("earlier prompt");
    send_key(&mut app, ctrl('r'));
    assert!(app.reverse_search.is_some());
    assert!(!shows_hint(&mut app));
}

#[test]
fn recording_hides_the_hint() {
    let mut app = stashed();
    app.voice.transcribe_state = TranscribeState::Recording;
    assert!(!shows_hint(&mut app));
}

#[test]
fn a_focused_subagent_list_hides_the_hint() {
    let mut app = stashed();
    app.subagents.list.focused = true;
    assert!(!shows_hint(&mut app));
}
