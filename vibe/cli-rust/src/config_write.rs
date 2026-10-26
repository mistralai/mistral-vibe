//! Shared `config/write` request with rejected/failed mutation handling.

use serde_json::{json, Value};

use crate::server::{method, Client};

/// The session-only config layer, as the server names it.
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
        .map_err(|error| crate::server::request_error::describe(&error))?;
    match mutation_error(&value) {
        Some(error) => Err(error),
        None => Ok(value),
    }
}

/// Write a saved model/thinking pick through `config/model/write`, which a
/// running turn parks until it ends; `Ok(true)` means parked. A server without
/// that method gets `fallback` through the idle-only `config/write`.
pub async fn write_model(
    client: &Client,
    session_id: &str,
    mut pick: Value,
    fallback: Vec<Value>,
    reason: &str,
) -> Result<bool, String> {
    pick["sessionId"] = json!(session_id);
    let value = match client.request_err(method::CONFIG_MODEL_WRITE, pick).await {
        Ok(value) => value,
        Err(error) if error.is_method_not_found() => {
            return write(client, session_id, fallback, reason)
                .await
                .map(|_| false);
        }
        Err(error) if error.message.is_empty() => return Err(error.code()),
        Err(error) => return Err(error.message),
    };
    match mutation_error(&value) {
        Some(error) => Err(error),
        None => Ok(value.get("status").and_then(Value::as_str) == Some("pending")),
    }
}

/// The error of a rejected or failed config mutation, if any.
pub fn mutation_error(value: &Value) -> Option<String> {
    if value
        .get("rejected")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        return Some(match value.get("reason").and_then(Value::as_str) {
            Some(reason) if !reason.is_empty() => format!("Invalid configuration edit: {reason}"),
            _ => "Invalid configuration edit".to_owned(),
        });
    }
    let failures: Vec<&str> = value
        .get("failures")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    (!failures.is_empty()).then(|| failures.join("; "))
}
