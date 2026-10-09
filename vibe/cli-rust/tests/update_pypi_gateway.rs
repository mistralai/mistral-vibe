//! The PyPI gateway's response parsing, over fixture JSON (no network).

use vibe_rs::update_notifier::gateway::{
    build_http_client, configure_tls_trust, default_gateway_message, parse_response,
    tls_trust_store, PyPIUpdateGateway, TlsTrustStore, UpdateGatewayCause,
};

fn versions(list: &[&str]) -> String {
    list.iter()
        .map(|version| format!("\"{version}\""))
        .collect::<Vec<_>>()
        .join(",")
}

fn files(list: &[(&str, bool)]) -> String {
    list.iter()
        .map(|(filename, yanked)| format!("{{\"filename\": \"{filename}\", \"yanked\": {yanked}}}"))
        .collect::<Vec<_>>()
        .join(",")
}

#[test]
fn picks_the_highest_version_with_a_non_yanked_file() {
    let body = format!(
        r#"{{"versions": [{}], "files": [{}]}}"#,
        versions(&["2.25.6", "2.25.10", "2.25.9"]),
        files(&[
            ("mistral_vibe-2.25.6-py3-none-any.whl", false),
            ("mistral_vibe-2.25.10-py3-none-any.whl", false),
            ("mistral_vibe-2.25.9.tar.gz", false),
        ])
    );
    let data: serde_json::Value = serde_json::from_str(&body).unwrap();
    let update = parse_response(&data).unwrap();
    assert_eq!(update.latest_version, "2.25.10");
}

#[test]
fn yanked_and_unfiled_versions_are_skipped() {
    let body = format!(
        r#"{{"versions": [{}], "files": [{}]}}"#,
        versions(&["2.26.0", "2.25.6"]),
        files(&[
            ("mistral_vibe-2.26.0-py3-none-any.whl", true),
            ("mistral_vibe-2.25.6-py3-none-any.whl", false),
            ("mistral_vibe-2.25.6.tar.gz", false),
        ])
    );
    let data: serde_json::Value = serde_json::from_str(&body).unwrap();
    let update = parse_response(&data).unwrap();
    assert_eq!(update.latest_version, "2.25.6");
}

#[test]
fn sdists_and_build_tags_count_as_files() {
    let body = format!(
        r#"{{"versions": [{}], "files": [{}]}}"#,
        versions(&["2.25.6"]),
        files(&[("mistral_vibe-2.25.6-1-py3-none-any.whl", false)])
    );
    let data: serde_json::Value = serde_json::from_str(&body).unwrap();
    let update = parse_response(&data).unwrap();
    assert_eq!(update.latest_version, "2.25.6");

    let body = format!(
        r#"{{"versions": [{}], "files": [{}]}}"#,
        versions(&["2.25.6"]),
        files(&[("mistral_vibe-2.25.6.zip", false)])
    );
    let data: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(parse_response(&data).unwrap().latest_version, "2.25.6");
}

#[test]
fn unparsable_entries_are_skipped_not_fatal() {
    let body = format!(
        r#"{{"versions": [{}, 3, "junk.9"], "files": [{}, {{"yanked": false}}]}}"#,
        versions(&["2.25.6"]),
        files(&[("mistral_vibe-2.25.6-py3-none-any.whl", false)])
    );
    let data: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(parse_response(&data).unwrap().latest_version, "2.25.6");
}

#[test]
fn absent_versions_or_files_yield_no_update() {
    let data: serde_json::Value = serde_json::from_str(r#"{"versions": ["2.25.6"]}"#).unwrap();
    assert_eq!(parse_response(&data), None);
    let data: serde_json::Value =
        serde_json::from_str(r#"{"files": [{"filename": "x-1.0.tar.gz"}]}"#).unwrap();
    assert_eq!(parse_response(&data), None);
    let data: serde_json::Value = serde_json::from_str("{}").unwrap();
    assert_eq!(parse_response(&data), None);
}

#[test]
fn default_messages_match_python_verbatim() {
    let expected = |cause: UpdateGatewayCause| match cause {
        UpdateGatewayCause::TooManyRequests => "Rate limit exceeded while checking for updates.",
        UpdateGatewayCause::Forbidden => "Request was forbidden while checking for updates.",
        UpdateGatewayCause::NotFound => {
            "Unable to fetch the releases. Please check your permissions."
        }
        UpdateGatewayCause::RequestFailed => "Network error while checking for updates.",
        UpdateGatewayCause::ErrorResponse => {
            "Unexpected response received while checking for updates."
        }
        UpdateGatewayCause::InvalidResponse => {
            "Received an invalid response while checking for updates."
        }
        UpdateGatewayCause::Unknown => "Unable to determine whether an update is available.",
    };
    for cause in [
        UpdateGatewayCause::TooManyRequests,
        UpdateGatewayCause::Forbidden,
        UpdateGatewayCause::NotFound,
        UpdateGatewayCause::RequestFailed,
        UpdateGatewayCause::ErrorResponse,
        UpdateGatewayCause::InvalidResponse,
        UpdateGatewayCause::Unknown,
    ] {
        assert_eq!(default_gateway_message(cause), expected(cause));
    }
}

/// Python's `base_url` constructor parameter: the default targets PyPI, and
/// only the constructor decides the gateway's URL — no env override, which
/// Python does not have either.
#[test]
fn gateway_urls_come_from_the_base_url_parameter() {
    assert_eq!(
        PyPIUpdateGateway::new("mistral-vibe").request_url(),
        "https://pypi.org/simple/mistral-vibe/"
    );
    assert_eq!(
        PyPIUpdateGateway::with_base_url("mistral-vibe", "http://localhost:8080/").request_url(),
        "http://localhost:8080/simple/mistral-vibe/"
    );
}

/// ADR 0015 / Python `configure_ssl_context`: the HTTP client's trust store
/// follows the effective `enable_system_trust_store`, and the client builds
/// under both policies.
#[test]
fn http_client_trust_follows_the_system_trust_store_setting() {
    configure_tls_trust(false);
    assert_eq!(tls_trust_store(), TlsTrustStore::BundledWebPki);
    assert!(build_http_client().is_ok());

    configure_tls_trust(true);
    assert_eq!(tls_trust_store(), TlsTrustStore::System);
    assert!(build_http_client().is_ok());

    configure_tls_trust(false);
    assert_eq!(tls_trust_store(), TlsTrustStore::BundledWebPki);
}
