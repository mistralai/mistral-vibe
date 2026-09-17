//! Shared `/demo` history fixtures, also repeated by `/stress`.

use serde_json::Value;

const HISTORY: &str = include_str!("../../../cli/textual_ui/demo_history.data");

pub fn entries() -> Vec<Value> {
    let mut entries: Vec<Value> = serde_json::from_str(HISTORY).expect("valid demo history JSON");
    assert!(!entries.is_empty(), "demo history must not be empty");
    for entry in &mut entries {
        entry["local"] = Value::Bool(true);
        entry["historical"] = Value::Bool(true);
    }
    entries
}

pub fn stress_entry(entries: &[Value], counter: u64) -> Value {
    let mut entry = entries[counter as usize % entries.len()].clone();
    let id = entry
        .get("id")
        .and_then(Value::as_str)
        .expect("demo history entry id");
    entry["id"] = Value::String(format!("stress-{counter:06}-{id}"));
    entry
}
