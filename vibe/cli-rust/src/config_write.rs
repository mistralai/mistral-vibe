//! Shared `config/write` request with Python's rejected/failed mutation handling.

use serde_json::{json, Value};

use crate::server::{method, Client};

pub fn set_op(path: &str, value: impl Into<Value>) -> Value {
    json!({"op": "set", "path": path, "value": value.into(), "targetLayer": null})
}

/// Write `ops` without reloading the runtime; a rejected or failed mutation is an error.
pub async fn write(
    client: &Client,
    session_id: &str,
    ops: Vec<Value>,
    reason: &str,
) -> Result<Value, String> {
    let params = json!({
        "sessionId": session_id,
        "ops": ops,
        "reason": reason,
        "reloadRuntime": false,
    });
    let value = client
        .request(method::CONFIG_WRITE, params)
        .await
        .map_err(|error| error.to_string())?;
    match mutation_error(&value) {
        Some(error) => Err(error),
        None => Ok(value),
    }
}

/// Python raises `AppServerResponseError` on a rejected or failed config mutation.
pub fn mutation_error(value: &Value) -> Option<String> {
    if value
        .get("rejected")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Some("Invalid configuration edit".to_owned());
    }
    let failures: Vec<&str> = value
        .get("failures")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    (!failures.is_empty()).then(|| failures.join("; "))
}
