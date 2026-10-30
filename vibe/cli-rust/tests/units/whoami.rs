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
    let text = whoami_text(
        &reads(json!({
            "status": "ready",
            "apiKey": {"preview": "mstrl_abcd****", "scope": "workspace", "accessScope": "shared_only"},
        })),
        None,
    );
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
    let text = whoami_text(
        &reads(json!({
            "status": "ready",
            "apiKey": {"preview": "abcd****", "scope": "vibe", "accessScope": null},
        })),
        None,
    );
    assert!(text.ends_with("- **Key scope**: vibe"), "{text}");
}

#[test]
fn omits_api_key_lines_without_api_key() {
    let text = whoami_text(&reads(json!({"status": "unavailable"})), None);
    assert_eq!(text, "## Who am I\n\n- **Email**: ada@example.com");
}

fn auth(sources: serde_json::Value) -> serde_json::Value {
    json!({
        "authKind": "api_key",
        "apiKeyEnvVar": "MISTRAL_API_KEY",
        "envFilePath": "/nonexistent-home/.vibe/.env",
        "sources": sources,
    })
}

#[test]
fn appends_the_credential_source_in_use() {
    let view = auth(json!([
        {"kind": "process_env", "state": "in_use"},
        {"kind": "os_keyring", "state": "available"},
    ]));
    let text = whoami_text(&reads(json!({"status": "unavailable"})), Some(&view));
    assert_eq!(
        text,
        "## Who am I\n\n\
         - **Email**: ada@example.com\n\
         - **Credential source (MISTRAL\\_API\\_KEY)**: Process environment"
    );
}

#[test]
fn names_the_source_without_an_identity() {
    let view = auth(json!([{"kind": "os_keyring", "state": "in_use"}]));
    let text = whoami_text(&AccountReads::default(), Some(&view));
    assert_eq!(
        text,
        "## Who am I\n\n\
         No Mistral account information is available.\n\n\
         - **Credential source (MISTRAL\\_API\\_KEY)**: OS keyring"
    );
}

#[test]
fn omits_the_source_line_when_none_is_in_use() {
    let view = auth(json!([{"kind": "os_keyring", "state": "available"}]));
    let text = whoami_text(&AccountReads::default(), Some(&view));
    assert!(!text.contains("Credential source"), "{text}");
    let adc = json!({"authKind": "google_adc"});
    assert!(!whoami_text(&AccountReads::default(), Some(&adc)).contains("Credential source"));
}
