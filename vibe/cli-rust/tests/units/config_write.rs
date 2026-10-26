//! `pick_ops` always writes the session layer, plus the user config when saved;
//! a rejected write's error carries the server's reason.

use serde_json::{json, Value};
use vibe_rs::config_write::{mutation_error, pick_ops, Scope};

fn layers(ops: &[Value]) -> Vec<Value> {
    ops.iter().map(|op| op["targetLayer"].clone()).collect()
}

#[test]
fn saved_pick_writes_user_config_and_session() {
    let ops = pick_ops("/active_model", "devstral", Scope::Saved);
    assert_eq!(layers(&ops), vec![Value::Null, json!("overrides")]);
    assert!(ops.iter().all(|op| op["value"] == "devstral"));
}

#[test]
fn session_pick_writes_session_only() {
    let ops = pick_ops("/active_model", "devstral", Scope::Session);
    assert_eq!(layers(&ops), vec![json!("overrides")]);
}

#[test]
fn a_rejected_write_shows_the_servers_reason() {
    let reason = "The 'lean' agent runs tools.bash with max_timeout_seconds 1200";
    let response = json!({"rejected": true, "reason": reason, "failures": []});
    assert_eq!(
        mutation_error(&response),
        Some(format!("Invalid configuration edit: {reason}"))
    );
}

#[test]
fn a_rejected_write_without_a_reason_keeps_the_generic_error() {
    let response = json!({"rejected": true, "failures": []});
    assert_eq!(
        mutation_error(&response),
        Some("Invalid configuration edit".to_owned())
    );
}
