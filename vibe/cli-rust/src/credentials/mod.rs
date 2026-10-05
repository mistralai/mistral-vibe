//! The client-side read-only credential read model (Python `vibe/utils/api_keys.py`):
//! env, dotenv, and OS keyring reads. Writes stay server-side through
//! setup/store-credential — no set/delete surface lives here.

pub mod dotenv;
pub mod keyring;

/// Python `resolve_api_key`: a non-empty process env value first (empty reads
/// as absent, Python truthiness), then the OS keyring — the single
/// `ai.mistral.vibe` service only; the legacy `vibe` migration stays on the
/// server path.
pub fn resolve_api_key(env_key: &str) -> Option<String> {
    if env_key.is_empty() {
        return None;
    }
    if let Some(value) = std::env::var_os(env_key).filter(|value| !value.is_empty()) {
        // A present value is present even when it is not UTF-8 (Python
        // `os.environ.get` never fails on the value).
        return Some(value.to_string_lossy().into_owned());
    }
    keyring::get_api_key_from_keyring(env_key)
}

/// Whether `MISTRAL_API_KEY` resolves at all (env or keyring): the cheap
/// pre-spawn probe (Python `require_api_key_or_onboarding`) that lets a
/// normal launch skip the pre-session `setup/status` and overlap the child
/// boot with the first draw. A positive probe never decides the key IS
/// valid: it only skips the pre-session check and defers to the session
/// handshake's typed missing-key verdict — the universal backstop that
/// session start, resume, continue, and fork all raise server-side.
pub fn default_key_present() -> bool {
    if resolve_api_key("MISTRAL_API_KEY").is_some() {
        return true;
    }
    // When VIBE_ACTIVE_MODEL is set, the active provider may not need a key
    // (e.g. local). Be optimistic: draw first, let the handshake's typed
    // MissingApiKey verdict be the universal backstop.
    if std::env::var_os("VIBE_ACTIVE_MODEL").is_some_and(|v| !v.is_empty()) {
        return true;
    }
    false
}
