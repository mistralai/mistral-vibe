//! The `/whoami` body renderer.

use serde_json::json;

use vibe_rs::commands::simple::whoami_text;
use vibe_rs::post_ready::AccountReads;

fn reads(account: serde_json::Value) -> AccountReads {
    AccountReads {
        identity: json!({"id": "user-1", "email": "ada@example.com"}),
        account,
    }
}

#[test]
fn renders_api_key_preview_and_scopes() {
    let text = whoami_text(&reads(json!({
        "status": "ready",
        "apiKey": {"preview": "mstrl_abcd****", "scope": "workspace", "accessScope": "shared_only"},
    })));
    assert_eq!(
        text,
        "## Who am I\n\n\
         - **Email**: ada@example.com\n\
         - **API key**: mstrl\\_abcd\\*\\*\\*\\*\n\
         - **Key scope**: workspace\n\
         - **Access scope**: shared\\_only"
    );
}

#[test]
fn omits_access_scope_when_backend_does_not_send_it() {
    let text = whoami_text(&reads(json!({
        "status": "ready",
        "apiKey": {"preview": "abcd****", "scope": "vibe", "accessScope": null},
    })));
    assert!(text.ends_with("- **Key scope**: vibe"), "{text}");
}

#[test]
fn omits_api_key_lines_without_api_key() {
    let text = whoami_text(&reads(json!({"status": "unavailable"})));
    assert_eq!(text, "## Who am I\n\n- **Email**: ada@example.com");
}
