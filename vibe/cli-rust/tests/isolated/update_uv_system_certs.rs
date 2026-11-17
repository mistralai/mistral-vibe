//! ADR 0015: update commands hand `enable_system_trust_store` to uv as `UV_SYSTEM_CERTS`.
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;

use vibe_rs::update_notifier::gateway::{configure_tls_trust, PyPIUpdateGateway, UpdateGateway};
use vibe_rs::update_notifier::update::spawn_update_command;
use vibe_rs::update_notifier::uv_oracle::{certificate_hint, UvOracleGateway};
use vibe_rs::utils::tls::uv_command_hint;

fn user_uv_env(system_certs: Option<&str>, native_tls: Option<&str>) {
    for (name, value) in [
        ("UV_SYSTEM_CERTS", system_certs),
        ("UV_NATIVE_TLS", native_tls),
    ] {
        match value {
            Some(value) => std::env::set_var(name, value),
            None => std::env::remove_var(name),
        }
    }
}

async fn uv_system_certs_seen() -> String {
    let (_, terminate) = tokio::sync::watch::channel(false);
    let outcome =
        spawn_update_command("echo \"${UV_SYSTEM_CERTS-unset}\"".to_owned(), terminate).await;
    assert!(outcome.succeeded);
    outcome.output
}

#[tokio::test]
async fn the_system_trust_store_reaches_uv() {
    user_uv_env(None, None);
    configure_tls_trust(true);
    assert_eq!(uv_system_certs_seen().await, "true");
}

#[tokio::test]
async fn the_bundled_roots_leave_uv_on_its_default() {
    user_uv_env(None, None);
    configure_tls_trust(false);
    assert_eq!(uv_system_certs_seen().await, "unset");
}

#[tokio::test]
async fn a_users_own_uv_system_certs_wins() {
    user_uv_env(Some("false"), None);
    configure_tls_trust(true);
    assert_eq!(uv_system_certs_seen().await, "false");
}

#[tokio::test]
async fn a_users_legacy_uv_native_tls_wins() {
    user_uv_env(None, Some("false"));
    configure_tls_trust(true);
    assert_eq!(uv_system_certs_seen().await, "unset");
}

#[tokio::test]
async fn an_empty_uv_system_certs_counts_as_unset() {
    user_uv_env(Some(""), None);
    configure_tls_trust(true);
    assert_eq!(uv_system_certs_seen().await, "true");
}

#[tokio::test]
async fn the_outdated_check_runs_uv_with_the_system_trust_store() {
    let bin = tempfile::tempdir().unwrap();
    let seen = bin.path().join("uv-system-certs");
    let uv = bin.path().join("uv");
    std::fs::write(
        &uv,
        format!(
            "#!/bin/sh\necho \"${{UV_SYSTEM_CERTS-unset}}\" > '{}'\n",
            seen.display()
        ),
    )
    .unwrap();
    std::fs::set_permissions(&uv, std::fs::Permissions::from_mode(0o755)).unwrap();
    let path = std::env::var("PATH").unwrap_or_default();
    std::env::set_var("PATH", format!("{}:{path}", bin.path().display()));
    user_uv_env(None, None);
    configure_tls_trust(true);

    let gateway = UvOracleGateway {
        fallback: PyPIUpdateGateway::new("mistral-vibe"),
    };
    assert_eq!(gateway.fetch_update().await.unwrap(), None);
    assert_eq!(std::fs::read_to_string(seen).unwrap().trim(), "true");
}

#[test]
fn printed_uv_commands_carry_system_certs_under_the_same_rule() {
    user_uv_env(None, None);
    configure_tls_trust(true);
    assert_eq!(
        uv_command_hint("uv tool upgrade mistral-vibe"),
        "uv --system-certs tool upgrade mistral-vibe"
    );
    assert_eq!(
        uv_command_hint("brew upgrade mistral-vibe"),
        "brew upgrade mistral-vibe"
    );
    user_uv_env(Some("true"), None);
    assert_eq!(
        uv_command_hint("uv tool upgrade mistral-vibe"),
        "uv tool upgrade mistral-vibe"
    );
    user_uv_env(None, None);
    configure_tls_trust(false);
    assert_eq!(
        uv_command_hint("uv tool upgrade mistral-vibe"),
        "uv tool upgrade mistral-vibe"
    );
}

#[test]
fn a_certificate_failure_points_at_the_trust_store_setting() {
    configure_tls_trust(false);
    assert!(
        certificate_hint("Caused by: invalid peer certificate: UnknownIssuer")
            .contains("enable_system_trust_store = true")
    );
    assert_eq!(
        certificate_hint("error: Request failed after 3 retries"),
        ""
    );
    configure_tls_trust(true);
    assert_eq!(
        certificate_hint("Caused by: invalid peer certificate: UnknownIssuer"),
        ""
    );
}
