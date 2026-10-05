//! The default-key probe (Python `os.environ.get` truthiness,
//! `vibe/utils/api_keys.py`): absent, empty, and set.

use vibe_rs::credentials::default_key_present;

/// `std::env::set_var` is process-global: the env-mutating tests serialize
/// (the credentials tests share this lock — they mutate the same vars).
pub(crate) static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// The keyring stays disabled for the whole test binary: an absent env key
/// must never read a real key from the developer's keychain (the
/// credentials tests reuse this — they mutate the same vars).
pub(crate) fn locked_and_keyring_disabled() -> std::sync::MutexGuard<'static, ()> {
    let guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    std::env::set_var("VIBE_TEST_DISABLE_KEYRING", "1");
    guard
}

#[test]
fn an_absent_key_reads_negative() {
    let _guard = locked_and_keyring_disabled();
    std::env::remove_var("MISTRAL_API_KEY");
    assert!(!default_key_present());
}

#[test]
fn an_empty_key_reads_absent() {
    let _guard = locked_and_keyring_disabled();
    // The dotenv load treats an empty shell value as unset, and so does the
    // probe: only a non-empty value may skip the pre-session check.
    std::env::set_var("MISTRAL_API_KEY", "");
    assert!(!default_key_present());
    std::env::remove_var("MISTRAL_API_KEY");
}

#[test]
fn a_set_key_reads_positive() {
    let _guard = locked_and_keyring_disabled();
    std::env::set_var("MISTRAL_API_KEY", "sk-mock-key");
    assert!(default_key_present());
    std::env::remove_var("MISTRAL_API_KEY");
}

#[test]
fn vibe_active_model_reads_positive_optimistically() {
    let _guard = locked_and_keyring_disabled();
    std::env::remove_var("MISTRAL_API_KEY");
    // When VIBE_ACTIVE_MODEL is set (e.g. "local"), the active provider may
    // not need a key. The probe is optimistic: draw first, let the
    // handshake's typed MissingApiKey verdict be the backstop.
    std::env::set_var("VIBE_ACTIVE_MODEL", "local");
    assert!(default_key_present());
    std::env::remove_var("VIBE_ACTIVE_MODEL");
}
