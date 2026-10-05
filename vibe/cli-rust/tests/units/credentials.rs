//! The credential read model (Python `resolve_api_key`): env first, empty
//! reads as absent. The live-keyring lookup itself is not unit-tested
//! against a real keychain — e2e runs pin `VIBE_TEST_DISABLE_KEYRING=1`.

use super::setup_probe::locked_and_keyring_disabled;
use vibe_rs::credentials::resolve_api_key;

#[test]
fn a_non_empty_env_value_resolves() {
    let _guard = locked_and_keyring_disabled();
    std::env::set_var("MISTRAL_API_KEY", "sk-mock-key");
    // Env wins over the keyring (trivially: the keyring is disabled here).
    assert_eq!(
        resolve_api_key("MISTRAL_API_KEY").as_deref(),
        Some("sk-mock-key")
    );
    std::env::remove_var("MISTRAL_API_KEY");
}

#[test]
fn an_absent_env_value_reads_keyless() {
    let _guard = locked_and_keyring_disabled();
    std::env::remove_var("MISTRAL_API_KEY");
    assert_eq!(resolve_api_key("MISTRAL_API_KEY"), None);
}

#[test]
fn an_empty_env_value_reads_absent() {
    let _guard = locked_and_keyring_disabled();
    // Python truthiness: an empty value falls through, here to a disabled
    // keyring, so the read is keyless.
    std::env::set_var("MISTRAL_API_KEY", "");
    assert_eq!(resolve_api_key("MISTRAL_API_KEY"), None);
    std::env::remove_var("MISTRAL_API_KEY");
}

/// Python `os.environ.get` treats a non-UTF-8 value as present, so the read
/// is lossy rather than absent (unix-only: it takes an `OsString` from bytes).
#[test]
#[cfg(unix)]
fn a_non_utf8_env_value_resolves_lossily() {
    use std::os::unix::ffi::OsStringExt;
    let _guard = locked_and_keyring_disabled();
    std::env::set_var(
        "MISTRAL_API_KEY",
        std::ffi::OsString::from_vec(vec![0xff, b'k']),
    );
    assert_eq!(
        resolve_api_key("MISTRAL_API_KEY").as_deref(),
        Some("\u{fffd}k")
    );
    std::env::remove_var("MISTRAL_API_KEY");
}
