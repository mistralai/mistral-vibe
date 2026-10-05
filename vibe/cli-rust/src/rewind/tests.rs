use super::*;

fn selected(app: &mut App, has_file_changes: bool) {
    apply_event(
        app,
        Event::Selected {
            entry_index: 0,
            entry_id: "entry-1".into(),
            preview: "fix the parser".into(),
            has_file_changes,
        },
    );
}

#[test]
fn the_action_step_offers_a_restore_only_when_files_changed() {
    let mut app = App::default();
    selected(&mut app, true);
    assert_eq!(options(&app).len(), 2);
    selected(&mut app, false);
    assert_eq!(options(&app), ["Edit message from here"]);
}

#[test]
fn navigation_wraps_around_the_current_step() {
    let mut app = App::default();
    selected(&mut app, true);
    navigate(&mut app, true);
    assert_eq!(app.rewind.selected, 1);
    navigate(&mut app, true);
    assert_eq!(app.rewind.selected, 0);
    navigate(&mut app, false);
    assert_eq!(app.rewind.selected, 1);
}

#[test]
fn a_new_selection_reopens_on_the_action_step() {
    let mut app = App::default();
    selected(&mut app, true);
    app.rewind.step = Step::Persistence;
    app.rewind.restore_files = true;
    app.rewind.selected = 1;
    selected(&mut app, true);
    assert!(app.rewind.open);
    assert_eq!(app.rewind.step, Step::Action);
    assert!(!app.rewind.restore_files);
    assert_eq!(app.rewind.selected, 0);
    assert_eq!(app.view.scroll_to_entry, Some(0));
}

#[test]
fn a_failed_read_leaves_rewind_mode_when_nothing_is_highlighted() {
    let mut app = App::default();
    apply_event(&mut app, Event::Failed("boom".into()));
    assert!(!app.rewind.open);
    assert!(!app.overlays.toasts.is_empty());
}

#[test]
fn a_done_rewind_restores_the_message_into_the_composer() {
    let mut app = App::default();
    selected(&mut app, false);
    let state = serde_json::json!({
        "eventId": 3,
        "session": {"id": "new-session-id"},
        "history": [],
    });
    apply_event(
        &mut app,
        Event::Done {
            message: "fix the parser".into(),
            restore_errors: vec!["Failed to restore file: a.py".into()],
            old_session_id: "old-session-id".into(),
            inplace: false,
            state: Box::new(serde_json::from_value(state).expect("state")),
        },
    );
    assert!(!app.rewind.open);
    assert_eq!(app.chat_input.input, "fix the parser");
    assert_eq!(app.chat_input.cursor, 0);
    assert_eq!(app.session.session_id.as_deref(), Some("new-session-id"));
    assert!(!app.overlays.toasts.is_empty());
    // The fork notice is the only entry left after the history was replaced.
    assert_eq!(app.view.transcript.lines().count(), 1);
}
