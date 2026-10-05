//! OS keyring API-key lookup (Python `vibe.utils.keyring`), the read half
//! only: macOS shells out to `security`; Linux and Windows use the keyring
//! crates. Writes stay server-side through setup/store-credential.

const KEYRING_SERVICE: &str = "ai.mistral.vibe";
const DISABLE_KEYRING_ENV_VAR: &str = "VIBE_TEST_DISABLE_KEYRING";

/// Read `env_key`'s API key from the keyring, or `None` when absent/disabled.
/// Single service only: the legacy `vibe` migration stays on the server path.
pub fn get_api_key_from_keyring(env_key: &str) -> Option<String> {
    if env_key.is_empty() || keyring_disabled() {
        return None;
    }
    find_generic_password(KEYRING_SERVICE, env_key)
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

#[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
fn find_generic_password(service: &str, account: &str) -> Option<String> {
    let (service, account) = (service.to_owned(), account.to_owned());
    on_keyring_thread(move || {
        Ok(keyring::Entry::new(&service, &account)
            .and_then(|entry| entry.get_password())
            .ok()
            .filter(|key| !key.is_empty()))
    })
    .ok()
    .flatten()
}

/// Python's `keyring` WinVaultKeyring naming, so keys written by either CLI
/// are visible to the other: the credential's target name is the bare
/// service (`ai.mistral.vibe`, UserName = the env key), with a different
/// user's credential moved to the compound `{username}@{service}` target.
#[cfg(target_os = "windows")]
fn find_generic_password(service: &str, account: &str) -> Option<String> {
    let (service, account) = (service.to_owned(), account.to_owned());
    on_keyring_thread(move || Ok(win_vault::find(&service, &account)))
        .ok()
        .flatten()
}

#[cfg(target_os = "windows")]
mod win_vault {
    use std::collections::HashMap;

    use keyring_core::Entry;

    /// A credential whose target name is `target` (the service, or the
    /// compound `{username}@{service}`).
    fn entry(target: &str) -> Result<Entry, keyring_core::Error> {
        // `new_with_modifiers` needs the default store; only the facade's
        // one-time init (`store_status`/`Entry::new`) installs it.
        if keyring::Entry::store_status().is_err() {
            return Err(keyring_core::Error::NoDefaultStore);
        }
        let modifiers = HashMap::from([("target", target)]);
        Entry::new_with_modifiers(target, target, &modifiers)
    }

    /// Python `_resolve_credential`: the service-target credential when its
    /// UserName matches, else the compound one.
    pub(super) fn find(service: &str, account: &str) -> Option<String> {
        if let Ok(service_entry) = entry(service) {
            let matched = service_entry
                .get_attributes()
                .map(|attrs| {
                    attrs
                        .get("username")
                        .is_some_and(|user| user.as_str() == account)
                })
                .unwrap_or(false);
            if matched {
                if let Some(key) = service_entry
                    .get_password()
                    .ok()
                    .filter(|key| !key.is_empty())
                {
                    return Some(key);
                }
            }
        }
        let compound = format!("{account}@{service}");
        entry(&compound)
            .and_then(|entry| entry.get_password())
            .ok()
            .filter(|key| !key.is_empty())
    }
}

/// Run a keyring call on a dedicated thread: the Secret Service round-trip
/// blocks its thread, and callers sit inside the async runtime.
#[cfg(not(target_os = "macos"))]
fn on_keyring_thread<T, F>(f: F) -> Result<T, String>
where
    F: FnOnce() -> Result<T, String> + Send + 'static,
    T: Send + 'static,
{
    std::thread::Builder::new()
        .name("vibe-keyring".into())
        .spawn(f)
        .map_err(|e| format!("can't start keyring thread: {e}"))?
        .join()
        .map_err(|_| "keyring thread panicked".to_string())?
}
