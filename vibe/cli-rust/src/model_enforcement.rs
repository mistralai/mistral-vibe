//! Admin-enforced `active_model`: a `/model` pick could not change it, so it is not written.

use serde_json::{json, Value};

use crate::config_write;
use crate::server::{method, Client};

/// Why a pick of an admin-pinned model is refused.
pub const ENFORCED_MESSAGE: &str =
    "'active_model' is enforced by your administrator. Contact your admin to change it.";

/// Write a pick, refusing a model change the administrator pins; `Ok(true)` means parked.
pub async fn write(
    client: &Client,
    session_id: &str,
    saved: Option<Value>,
    ops: Vec<Value>,
    reason: &str,
) -> Result<bool, String> {
    let picks_model = ops.iter().any(|op| op["path"] == "/active_model");
    if picks_model && is_active_model_enforced(client, session_id).await {
        return Err(ENFORCED_MESSAGE.to_owned());
    }
    match saved {
        Some(pick) => config_write::write_model(client, session_id, pick, ops, reason).await,
        None => config_write::write(client, session_id, ops, reason)
            .await
            .map(|_| false),
    }
}

/// Whether the admin layer sets `active_model`; an unreadable answer does not block the pick.
pub async fn is_active_model_enforced(client: &Client, session_id: &str) -> bool {
    client
        .request(
            method::CONFIG_FIELDS_READ,
            json!({ "sessionId": session_id }),
        )
        .await
        .is_ok_and(|fields| is_enforced(&fields))
}

/// Whether a `config/fields/read` answer resolves `active_model` from the admin layer.
pub fn is_enforced(fields: &Value) -> bool {
    crate::config_fields::parse(fields)
        .fields
        .iter()
        .any(|field| field.name == "active_model" && !field.writable)
}
