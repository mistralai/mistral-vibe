//! Effect result-body formatting: todo buckets, shell transcript, sanitized bodies.

use serde_json::json;
use vibe_rs::server::effect_output::{format_effect_output, todo_rows};

#[test]
fn todo_rows_bucket_statuses_in_widget_order() {
    let output = json!({"todos": [
        {"id": "1", "content": "Write", "status": "completed"},
        {"id": "2", "content": "Run", "status": "in_progress"},
        {"id": "3", "content": "File", "status": "pending"},
        {"id": "4", "content": "Port", "status": "cancelled"},
        {"id": "5", "content": "Stray", "status": "weird"}
    ]});
    let todos = todo_rows(&output);
    let texts: Vec<&str> = todos.iter().map(|row| row.text.as_str()).collect();
    assert_eq!(
        texts,
        ["☐ Run", "☐ File", "☑ Write", "☒ Port"],
        "an unknown status is dropped, like Python's bucket filter"
    );
}

#[test]
fn todo_rows_render_no_todos_when_the_list_is_empty() {
    assert_eq!(todo_rows(&json!({"todos": []}))[0].text, "No todos");
    assert_eq!(todo_rows(&json!({}))[0].text, "No todos");
}

#[test]
fn shell_output_falls_back_to_no_content() {
    let output = json!({"stdout": "", "stderr": "", "output": ""});
    assert_eq!(
        format_effect_output(Some("shell"), Some(&output)),
        ["(no content)"]
    );
    let blank = json!({"stdout": "\n\n", "stderr": "", "output": ""});
    assert_eq!(
        format_effect_output(Some("shell"), Some(&blank)),
        ["(no content)"]
    );
    let text = json!({"stdout": "done", "stderr": "", "output": ""});
    assert_eq!(format_effect_output(Some("shell"), Some(&text)), ["done"]);
    let ansi = json!({"stdout": "\x1b[31mdone\x1b[0m", "stderr": "", "output": ""});
    assert_eq!(format_effect_output(Some("shell"), Some(&ansi)), ["done"]);
    let streamed = json!({"stdout": "done", "stderr": "err", "output": "streamed"});
    assert_eq!(
        format_effect_output(Some("shell"), Some(&streamed)),
        ["streamed"]
    );
    let split = json!({"stdout": "out", "stderr": "err", "output": ""});
    assert_eq!(
        format_effect_output(Some("shell"), Some(&split)),
        ["out", "err"]
    );
    let merged = json!({"stdout": "out\n", "stderr": "err", "output": ""});
    assert_eq!(
        format_effect_output(Some("shell"), Some(&merged)),
        ["out", "err"]
    );
}

#[test]
fn generic_output_is_sanitized() {
    let ansi = json!({"result": "\x1b[31mhello\x1b[0m"});
    assert_eq!(
        format_effect_output(Some("mcp"), Some(&ansi)),
        ["result: hello"]
    );
    let control_only = json!({"content": "\x1b[2K\r"});
    assert_eq!(
        format_effect_output(Some("custom"), Some(&control_only)),
        ["content: "]
    );
    let bare = json!("\x1b[2K\r");
    assert!(format_effect_output(Some("custom"), Some(&bare)).is_empty());
}

#[test]
fn subagent_output_is_sanitized() {
    // `TaskResult.response` comes from child `AssistantEvent.content`; Python `_yield_text` sanitizes it.
    let ansi = json!({"response": "\x1b[31mdone\x1b[0m", "completed": true});
    assert_eq!(
        format_effect_output(Some("subagent"), Some(&ansi)),
        ["response: done", "completed: True"]
    );
    let control = json!("\x1b[2K\r");
    assert!(format_effect_output(Some("subagent"), Some(&control)).is_empty());
}
