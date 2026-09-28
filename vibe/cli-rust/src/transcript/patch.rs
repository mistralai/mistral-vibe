//! JSON-Patch application for transcript entries (Python `apply_json_patch`).

use serde_json::Value;

/// Apply the JSON-Patch ops to an entry (Python `apply_json_patch`).
pub fn apply_json_patch(root: &mut Value, ops: &[Value]) {
    for op in ops {
        apply_operation(root, op);
    }
}

/// The text appended at `path` by this patch (Python `_appended_text`).
pub(crate) fn appended_text(ops: &[Value], path: &str) -> String {
    ops.iter()
        .filter(|op| {
            op.get("op").and_then(Value::as_str) == Some("append")
                && op.get("path").and_then(Value::as_str) == Some(path)
        })
        .filter_map(|op| op.get("value").and_then(Value::as_str))
        .collect()
}

/// Apply one op Vibe emits (add/replace/remove/append/test).
fn apply_operation(root: &mut Value, op: &Value) {
    let (Some(kind), Some(path)) = (
        op.get("op").and_then(Value::as_str),
        op.get("path").and_then(Value::as_str),
    ) else {
        return;
    };
    let value = op.get("value").cloned().unwrap_or(Value::Null);

    match kind {
        "add" | "replace" => {
            if let Some(slot) = pointer_mut(root, path) {
                *slot = value;
            }
        }
        "append" => {
            if let Some(slot) = pointer_mut(root, path) {
                match (slot, value) {
                    (Value::String(s), Value::String(add)) => s.push_str(&add),
                    (Value::Array(arr), add) => arr.push(add),
                    _ => {}
                }
            }
        }
        "remove" => {
            // PoC: only handle removing an object key (last path segment).
            if let Some((Value::Object(map), key)) = split_pointer(root, path) {
                map.remove(&key);
            }
        }
        "test" => {} // no-op in PoC
        _ => {}
    }
}

/// Resolve a JSON Pointer to a mutable slot, creating missing object keys.
fn pointer_mut<'a>(root: &'a mut Value, pointer: &str) -> Option<&'a mut Value> {
    if pointer.is_empty() {
        return Some(root);
    }
    let mut cur = root;
    for raw in pointer.trim_start_matches('/').split('/') {
        let token = unescape(raw);
        cur = match cur {
            Value::Object(map) => map.entry(token).or_insert(Value::Null),
            Value::Array(arr) => {
                let idx: usize = token.parse().ok()?;
                arr.get_mut(idx)?
            }
            _ => return None,
        };
    }
    Some(cur)
}

/// Split a pointer into (parent slot, last key) for removal.
fn split_pointer<'a>(root: &'a mut Value, pointer: &str) -> Option<(&'a mut Value, String)> {
    let trimmed = pointer.trim_start_matches('/');
    let (parent_path, last) = match trimmed.rsplit_once('/') {
        Some((p, l)) => (format!("/{p}"), unescape(l)),
        None => (String::new(), unescape(trimmed)),
    };
    let parent = pointer_mut(root, &parent_path)?;
    Some((parent, last))
}

fn unescape(token: &str) -> String {
    token.replace("~1", "/").replace("~0", "~")
}
