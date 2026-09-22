//! macOS Keychain API-key lookup (Python `vibe.utils.keyring`).

const KEYRING_SERVICE: &str = "ai.mistral.vibe";
const LEGACY_SERVICES: [&str; 1] = ["vibe"];
const DISABLE_KEYRING_ENV_VAR: &str = "VIBE_TEST_DISABLE_KEYRING";

/// Read `env_key`'s API key from the Keychain, or `None` when absent/disabled.
pub fn get_api_key_from_keyring(env_key: &str) -> Option<String> {
    if env_key.is_empty() || keyring_disabled() {
        return None;
    }
    for service in std::iter::once(KEYRING_SERVICE).chain(LEGACY_SERVICES) {
        if let Some(key) = find_generic_password(service, env_key) {
            return Some(key);
        }
    }
    None
}

fn keyring_disabled() -> bool {
    std::env::var(DISABLE_KEYRING_ENV_VAR).ok().as_deref() == Some("1")
}

#[cfg(target_os = "macos")]
fn find_generic_password(service: &str, account: &str) -> Option<String> {
    let output = std::process::Command::new("/usr/bin/security")
        .args(["find-generic-password", "-s", service, "-a", account, "-w"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let key = String::from_utf8(output.stdout).ok()?;
    let key = key.strip_suffix('\n').unwrap_or(&key);
    if key.is_empty() {
        None
    } else {
        Some(key.to_string())
    }
}

#[cfg(not(target_os = "macos"))]
fn find_generic_password(_service: &str, _account: &str) -> Option<String> {
    None
}
