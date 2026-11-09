//! Headless startup guards: keyring preflight notice and startup deadline.

use std::future::Future;
use std::time::Duration;

use anyhow::{anyhow, Result};

/// Bound on initialize + session/start; the app-server's own keyring reads give up after 60s.
pub const STARTUP_TIMEOUT: Duration = Duration::from_secs(120);

const KEYRING_NAME: &str = if cfg!(target_os = "macos") {
    "macOS Keychain"
} else {
    "system keyring"
};

/// The stderr line announcing that the app-server will read the key from the keyring.
pub fn keyring_preflight_notice(key_in_env: bool, keyring_disabled: bool) -> Option<String> {
    if key_in_env || keyring_disabled {
        return None;
    }
    Some(format!(
        "Reading API keys from the {KEYRING_NAME} (set them as environment variables to skip this)"
    ))
}

pub fn print_keyring_preflight() {
    let key_in_env = std::env::var_os("MISTRAL_API_KEY").is_some_and(|value| !value.is_empty());
    let disabled = crate::credentials::keyring::keyring_disabled();
    if let Some(notice) = keyring_preflight_notice(key_in_env, disabled) {
        eprintln!("{notice}");
    }
}

/// Fail the run when startup outlives `timeout` instead of waiting on a prompt nobody sees.
pub async fn within_startup_deadline<T>(
    timeout: Duration,
    startup: impl Future<Output = Result<T>>,
) -> Result<T> {
    tokio::time::timeout(timeout, startup)
        .await
        .unwrap_or_else(|_| {
            Err(anyhow!(
                "The app-server did not start within {}s. If a {KEYRING_NAME} prompt is waiting, \
             approve it or set the provider's API key environment variable.",
                timeout.as_secs()
            ))
        })
}
