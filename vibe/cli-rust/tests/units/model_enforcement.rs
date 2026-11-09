//! Only an admin-layer `active_model` blocks a `/model` pick; the blocked pick is in model/model_enforced.

use serde_json::json;
use vibe_rs::model_enforcement::is_enforced;

fn active_model(layers: serde_json::Value) -> serde_json::Value {
    json!({"fields": [{
        "name": "active_model",
        "kind": "enum",
        "description": "",
        "value": "alpha",
        "path": "/active_model",
        "layerValues": layers,
    }], "targets": ["user-toml"]})
}

#[test]
fn a_user_or_default_active_model_is_not_enforced() {
    assert!(!is_enforced(&active_model(
        json!([{"layer": "user-toml", "value": "alpha"}])
    )));
    assert!(!is_enforced(&active_model(json!([]))));
    assert!(!is_enforced(&json!({})));
}
