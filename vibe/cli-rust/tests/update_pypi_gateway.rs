//! The PyPI gateway's response parsing, over fixture JSON (no network).

use vibe_rs::update_notifier::gateway::{
    build_http_client, configure_tls_trust, custom_root_certificates, default_gateway_message,
    parse_response, tls_trust_store, PyPIUpdateGateway, TlsTrustStore, UpdateGatewayCause,
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

/// A self-signed certificate, valid PEM for the loader tests below.
const TEST_CERT_PEM: &str = "-----BEGIN CERTIFICATE-----
MIICrjCCAZYCCQDm6Adit+75AjANBgkqhkiG9w0BAQsFADAYMRYwFAYDVQQDDA12
aWJlLXRscy10ZXN0MCAXDTI2MDkyMjEzMzAwNloYDzIxMjYwODI5MTMzMDA2WjAY
MRYwFAYDVQQDDA12aWJlLXRscy10ZXN0MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A
MIIBCgKCAQEA9EU5Ckg1bT6rGKW49+1T038SllYooIpYWmzHVvyu/4iCgSPu2JiW
BTH2tt2HrI49kGsRRO7z3OcuxS48wFKWsw+eFtwWof52BRFSn7OzFhlg7l+HCoQq
TrxY3KwIpPkp3Yr4DXXhbsJKVFeDUp2CP/LPK/bjdWFyEl2QMIxWoZOQEJ6rWMnD
kGUAtcWqXKVVKk06B/T14etc5f0Nd38TRtl1bh0KZdN9EpcBrqHTH3XqknEBxeeU
2VjXnWJ9a5yNFsXC/BlLxzy+cDADkow2Rz0Kf4O0nNDRalJALHwElRtS+00/l5qd
As1BX4XHtagnJZx0iFBViMnh6PZDoTx1sQIDAQABMA0GCSqGSIb3DQEBCwUAA4IB
AQBCdhrOFmFrqyDQu61OCzq9f7lquxgjMbTKUQXOpv2jU4T4Y84Djr9Um92tnymS
j04CA8aamvRUbCyehMjBxPVf3IFl9qzKO0mRsZBfjarBnvkHCg40mf2FBlorkw8G
r0xlc8J1mPjq+4yohORIsRRzq3jXkIHNR/ll2+b1/GW9hVmhVnfWT3tTmekvkz5B
bvJEGlu4h5nrsH4Cdt/PntgR+IX3tdufKamULQsjxaNfgw3jR/VZnOnb3r8nsc2G
5XkpsOigbqQJ0Toshe+LzcZo6iOtkAAbkA2xWem3q1i5FxQ1Z1Havt7Rli8CXjgk
2hPr6BHtwzHSWsAu83gwC9+H
-----END CERTIFICATE-----
";

/// Restores an env var to its pre-test value on drop.
struct RestoreEnv(&'static str, Option<std::ffi::OsString>);

impl RestoreEnv {
    fn set(name: &'static str, value: &str) -> Self {
        let previous = std::env::var_os(name);
        std::env::set_var(name, value);
        Self(name, previous)
    }

    fn unset(name: &'static str) -> Self {
        let previous = std::env::var_os(name);
        std::env::remove_var(name);
        Self(name, previous)
    }
}

impl Drop for RestoreEnv {
    fn drop(&mut self) {
        match self.1.take() {
            Some(value) => std::env::set_var(self.0, value),
            None => std::env::remove_var(self.0),
        }
    }
}

/// ADR 0015: `SSL_CERT_FILE` and `SSL_CERT_DIR` certificates load additively
/// in front of the policy roots, unparsable files are skipped, and an empty
/// value counts as unset like Python's truthiness check.
#[test]
fn custom_roots_load_from_ssl_cert_file_and_dir() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("ca.pem"), TEST_CERT_PEM).unwrap();
    std::fs::write(dir.path().join("junk.txt"), "not a certificate").unwrap();

    let _cert_file = RestoreEnv::set(
        "SSL_CERT_FILE",
        &dir.path().join("ca.pem").to_string_lossy(),
    );
    let _cert_dir = RestoreEnv::set("SSL_CERT_DIR", dir.path().to_string_lossy().as_ref());
    assert_eq!(custom_root_certificates().len(), 2);

    // An unparsable bundle contributes nothing without failing the call.
    let _junk = RestoreEnv::set(
        "SSL_CERT_FILE",
        &dir.path().join("junk.txt").to_string_lossy(),
    );
    assert_eq!(custom_root_certificates().len(), 1);

    // Empty values count as unset, like Python's `if ssl_cert_file or ...`.
    let _empty = RestoreEnv::set("SSL_CERT_DIR", "");
    let _unset = RestoreEnv::unset("SSL_CERT_FILE");
    assert!(custom_root_certificates().is_empty());

    // A c_rehash-style CApath is made of hashed symlinks to the real
    // certificates; OpenSSL — and Python through it — loads them, so entries
    // that resolve to a regular file must load too, while a symlink to a
    // directory still does not.
    let hashed = tempfile::tempdir().unwrap();
    let certs = tempfile::tempdir().unwrap();
    std::fs::write(certs.path().join("corp-ca.pem"), TEST_CERT_PEM).unwrap();
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(
            certs.path().join("corp-ca.pem"),
            hashed.path().join("d34a3e4b.0"),
        )
        .unwrap();
        let _hashed_dir = RestoreEnv::set("SSL_CERT_DIR", hashed.path().to_string_lossy().as_ref());
        assert_eq!(custom_root_certificates().len(), 1);

        std::os::unix::fs::symlink(certs.path(), hashed.path().join("subdir-link")).unwrap();
        assert_eq!(custom_root_certificates().len(), 1);

        // A broken link resolves to nothing and is skipped like a file that
        // cannot be read.
        std::os::unix::fs::symlink(
            certs.path().join("missing.pem"),
            hashed.path().join("deadbeef.0"),
        )
        .unwrap();
        assert_eq!(custom_root_certificates().len(), 1);
    }
    #[cfg(not(unix))]
    {
        let _hashed_dir = RestoreEnv::set("SSL_CERT_DIR", hashed.path().to_string_lossy().as_ref());
        assert!(custom_root_certificates().is_empty());
    }
}
