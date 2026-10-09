//! `pick_ops` always writes the session layer, plus the user config when saved.

use serde_json::{json, Value};
use vibe_rs::config_write::{pick_ops, Scope};

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
