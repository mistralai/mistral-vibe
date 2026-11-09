use super::*;

fn state(id: &str) -> Value {
    serde_json::json!({"eventId": 7, "session": {"id": id}, "history": []})
}

#[test]
fn done_reads_the_rewind_response() {
    let result = serde_json::json!({
        "message": "hello",
        "restoreErrors": ["Failed to restore file: a.py"],
        "restoredPaths": ["a.py"],
        "state": state("new-session"),
    });
    let Event::Done {
        message,
        restore_errors,
        old_session_id,
        inplace,
        state,
    } = done(result, "old-session".into(), false)
    else {
        panic!("expected a done event");
    };
    assert_eq!(message, "hello");
    assert_eq!(restore_errors, ["Failed to restore file: a.py"]);
    assert_eq!(old_session_id, "old-session");
    assert!(!inplace);
    assert_eq!(state.session.id, "new-session");
}

#[test]
fn done_fails_without_state() {
    let result = serde_json::json!({"message": "hello"});
    assert!(matches!(done(result, "old".into(), true), Event::Failed(_)));
}
