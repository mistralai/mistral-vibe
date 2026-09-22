//! Effect wire types: header summary, body presence, and warning projection.

use serde_json::{json, Value};
use vibe_rs::server::EffectEntry;

fn effect(entry: Value) -> EffectEntry {
    serde_json::from_value(entry).unwrap()
}

#[test]
fn summary_reads_settled_suffix_and_running_status_text() {
    let running = effect(json!({
        "detail": {"kind": "shell", "display": {"verb": "Running",
            "message": "tail app.log", "statusText": "Running command"}},
        "state": {"status": "running", "outputText": "42%"}
    }));
    assert_eq!(running.summary(), ("Running", "tail app.log", ""));
    assert_eq!(running.output_text(), "42%");
    assert_eq!(running.status_text(), "Running command");
    assert!(!running.has_body());

    let settled = effect(json!({
        "detail": {"kind": "web_fetch", "display": {"verb": "Fetching", "message": "url"}},
        "state": {"status": "completed", "display": {"success": true,
            "verb": "Fetched", "message": "url", "suffix": "(truncated)"}}
    }));
    assert_eq!(settled.summary(), ("Fetched", "url", "(truncated)"));
}

#[test]
fn has_body_counts_output_fallback_text_and_warnings() {
    let base = json!({"detail": {"kind": "file_read", "display": {"verb": "Read"}}});
    let entry = |state: Value| effect(json!({"detail": base["detail"], "state": state}));

    let read = entry(json!({"status": "completed",
        "output": {"filePath": "app.py", "content": "x"}}));
    assert!(read.has_body());
    assert_eq!(read.file_read_path(), Some("app.py"));
    assert_eq!(read.file_write_path(), None);

    let warnings = entry(json!({"status": "completed",
        "output": {"filePath": "app.py", "content": "x"},
        "display": {"warnings": ["over quota"]}}));
    assert_eq!(warnings.result_warnings()[0], "over quota");

    // The result widget only exists when the output does, so a warnings-only
    // entry is expandable but renders no warning rows.
    let no_output = entry(json!({"status": "completed", "display": {
        "warnings": ["over quota"]}}));
    assert!(no_output.has_body());
    assert!(no_output.result_warnings().is_empty());

    // Grep's widget yields its warnings ahead of its null-result guard.
    let grep = effect(json!({
        "detail": {"kind": "file_search", "display": {"verb": "Searched"}},
        "state": {"status": "completed", "display": {"warnings": ["over quota"]}}
    }));
    assert_eq!(grep.result_warnings()[0], "over quota");

    let fallback = entry(json!({"status": "completed", "outputText": "deny reason"}));
    assert!(fallback.has_body());
    assert!(fallback.result_warnings().is_empty());

    let bare = entry(json!({"status": "completed", "display": {"success": true}}));
    assert!(!bare.has_body());
    assert!(!bare.is_muted() && bare.is_terminal());
}

#[test]
fn body_renders_error_reason_and_output_text_fallback() {
    let base = json!({"kind": "shell", "display": {"verb": "Ran"}});
    let effect = |state: Value| effect(json!({"detail": base, "state": state}));

    let failed = effect(json!({"status": "failed", "error": {"message": "boom"}}));
    assert_eq!(failed.body(), ["Error: boom"]);

    let skipped = effect(json!({"status": "skipped", "reason": "hook deny"}));
    assert_eq!(skipped.body(), ["Skipped: hook deny"]);

    let fallback = effect(json!({"status": "completed", "outputText": "deny reason"}));
    assert_eq!(fallback.body(), ["deny reason"]);
}

#[test]
fn subagent_body_keeps_status_paths_and_generic_output() {
    let spawn = json!({
        "kind": "subagent",
        "toolName": "subagent.spawn",
        "display": {"verb": "Explored"}
    });
    let wait = json!({
        "kind": "tool",
        "toolName": "subagent.wait",
        "display": {"verb": "Waited"}
    });
    let effect = |detail: Value, state: Value| effect(json!({"detail": detail, "state": state}));

    let failed = effect(
        spawn.clone(),
        json!({"status": "failed", "error": {"message": "boom"}}),
    );
    assert_eq!(failed.body(), ["Error: boom"]);

    let skipped = effect(
        spawn.clone(),
        json!({"status": "skipped", "reason": "hook deny"}),
    );
    assert_eq!(skipped.body(), ["Skipped: hook deny"]);

    let cancelled = effect(
        wait.clone(),
        json!({"status": "cancelled", "reason": "user"}),
    );
    assert_eq!(cancelled.body(), ["Skipped: user"]);

    let fallback = effect(
        spawn.clone(),
        json!({"status": "completed", "outputText": "deny reason"}),
    );
    assert_eq!(fallback.body(), ["deny reason"]);

    let success = effect(
        spawn,
        json!({"status": "completed", "output": {
            "response": "\x1b[31mdone\x1b[0m", "completed": true
        }}),
    );
    assert_eq!(success.body(), ["response: done", "completed: True"]);

    let wait_ok = effect(
        wait,
        json!({"status": "completed", "output": {"type": "success"}}),
    );
    assert_eq!(wait_ok.body(), ["type: success"]);
}
