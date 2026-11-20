//! Todo wire parsing, progress and the tracked list.

use serde_json::json;
use vibe_rs::todo_tracker::{self, TodoItem, TodoStatus};

fn item(id: &str, content: &str, status: TodoStatus) -> TodoItem {
    TodoItem {
        id: id.to_owned(),
        content: content.to_owned(),
        status,
    }
}

#[test]
fn parses_a_settled_todo_effect() {
    let entry = json!({
        "type": "effect",
        "detail": {"kind": "todo"},
        "state": {
            "status": "completed",
            "output": {"todos": [
                {"id": "1", "content": "Write the parity suite", "status": "completed"},
                {"id": "2", "content": "Run the parity suite", "status": "in_progress"},
                {"id": "3", "content": "File the follow-ups", "status": "pending"},
                {"id": "4", "content": "Port the narrator", "status": "cancelled"},
            ]},
        },
    });
    assert_eq!(
        todo_tracker::todos_from_entry(&entry),
        Some(vec![
            item("1", "Write the parity suite", TodoStatus::Completed),
            item("2", "Run the parity suite", TodoStatus::InProgress),
            item("3", "File the follow-ups", TodoStatus::Pending),
            item("4", "Port the narrator", TodoStatus::Cancelled),
        ])
    );
}

#[test]
fn ignores_other_effects_and_unsettled_states() {
    let other_kind = json!({
        "type": "effect",
        "detail": {"kind": "file_read"},
        "state": {"status": "completed", "output": {"todos": []}},
    });
    assert_eq!(todo_tracker::todos_from_entry(&other_kind), None);
    let running = json!({
        "type": "effect",
        "detail": {"kind": "todo"},
        "state": {"status": "in_progress"},
    });
    assert_eq!(todo_tracker::todos_from_entry(&running), None);
    let invalid_status = json!({
        "type": "effect",
        "detail": {"kind": "todo"},
        "state": {"status": "completed", "output": {"todos": [
            {"id": "1", "content": "Bad", "status": "weird"},
        ]}},
    });
    assert_eq!(todo_tracker::todos_from_entry(&invalid_status), None);
}

#[test]
fn progress_label_keeps_cancelled_out_of_the_denominator() {
    let todos = vec![
        item("1", "a", TodoStatus::Completed),
        item("2", "b", TodoStatus::Completed),
        item("3", "c", TodoStatus::Cancelled),
    ];
    assert_eq!(todo_tracker::progress_label(&todos), "2/2");
}

#[test]
fn tracker_records_and_clears() {
    let mut tracker = todo_tracker::TodoTracker::default();
    assert!(tracker.todos().is_empty());
    tracker.record(vec![item("1", "a", TodoStatus::InProgress)]);
    assert_eq!(tracker.todos().len(), 1);
    tracker.clear();
    assert!(tracker.todos().is_empty());
}
