//! The rate-limit failure renders Python's friendly message, not the raw error text.

use serde_json::{json, Value};
use vibe_rs::event_handler::rate_limit_message;

#[test]
fn without_a_rate_limit_action_the_base_message_shows() {
    let base = "Rate limits exceeded. Please wait a moment before trying again.";
    assert_eq!(rate_limit_message(&json!({"rateLimitAction": null})), base);
    assert_eq!(rate_limit_message(&json!({"status": "unavailable"})), base);
    assert_eq!(rate_limit_message(&Value::Null), base);
}

#[test]
fn with_a_rate_limit_action_the_upgrade_line_shows() {
    assert_eq!(
        rate_limit_message(&json!({"rateLimitAction": {"kind": "upgrade_to_pro"}})),
        "Rate limits exceeded. Please wait a moment before trying again, \
         or upgrade to Pro for higher rate limits and uninterrupted access."
    );
}
