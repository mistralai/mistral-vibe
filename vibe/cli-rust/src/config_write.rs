//! Shared `config/write` request with Python's rejected/failed mutation handling.

use serde_json::{json, Value};

use crate::server::{method, Client};

/// The session-only config layer (Python `OverridesLayer.NAME`).
pub const OVERRIDES_LAYER: &str = "overrides";

/// Where a model or thinking pick lands: saved to the user config, or this session only.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Scope {
    #[default]
    Saved,
    Session,
}

pub fn set_op(path: &str, value: impl Into<Value>) -> Value {
    set_op_in(path, value, None)
}

fn set_op_in(path: &str, value: impl Into<Value>, target_layer: Option<&str>) -> Value {
    json!({"op": "set", "path": path, "value": value.into(), "targetLayer": target_layer})
}

/// A pick always lands in the session layer, so it wins over a prior session-only pick.
pub fn pick_ops(path: &str, value: &str, scope: Scope) -> Vec<Value> {
    let session = set_op_in(path, value, Some(OVERRIDES_LAYER));
    match scope {
        Scope::Saved => vec![set_op(path, value), session],
        Scope::Session => vec![session],
    }
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
