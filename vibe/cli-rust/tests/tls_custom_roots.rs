//! ADR 0015: custom root certificates load from `SSL_CERT_FILE` and
//! `SSL_CERT_DIR`, over fixture PEM files (no network).
//!
//! This test mutates process-global environment variables, so it lives in
//! its own test binary (see AGENTS.md): a parallel test in another binary
//! could otherwise observe the mutated env, e.g. `build_http_client` loading
//! zero native certs while `SSL_CERT_FILE` points at an unparsable file.

use vibe_rs::update_notifier::gateway::custom_root_certificates;

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
-----END CERTIFICATE-----\n";

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
