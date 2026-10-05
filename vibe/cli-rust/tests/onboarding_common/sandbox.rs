//! The VIBE_HOME sandbox for the config-verifying scenarios. Included per
//! test binary via `#[path]`, so the env lock is per-process.

use std::path::PathBuf;

/// `std::env::set_var` is process-global and the scenarios persist into
/// `VIBE_HOME`: the env-mutating tests serialize and never touch the real
/// home. One lock per test binary (the module is included per binary).
static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// A sandboxed `VIBE_HOME` with the keyring disabled and browser opens
/// recorded to a log instead of spawned. Every env mutation is undone on
/// drop (the client persists nothing itself anymore, but the wizard must
/// never see the real home either).
pub struct Sandbox {
    /// Holds the temp dir alive for the sandbox's lifetime (never read).
    pub _home: tempfile::TempDir,
    pub action_log: PathBuf,
    _guard: std::sync::MutexGuard<'static, ()>,
}

pub fn sandbox() -> Sandbox {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    let home = tempfile::tempdir().expect("temp home");
    std::fs::create_dir_all(home.path().join("logs")).expect("logs dir");
    let action_log = home.path().join("logs").join("actions.jsonl");
    std::env::set_var("VIBE_HOME", home.path());
    std::env::set_var("VIBE_TEST_DISABLE_KEYRING", "1");
    std::env::set_var("VIBE_E2E_ACTION_LOG", &action_log);
    Sandbox {
        _home: home,
        action_log,
        _guard,
    }
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        std::env::remove_var("VIBE_HOME");
        std::env::remove_var("VIBE_TEST_DISABLE_KEYRING");
        std::env::remove_var("VIBE_E2E_ACTION_LOG");
        std::env::remove_var("MISTRAL_API_KEY");
    }
}

impl Sandbox {
    /// The recorded browser opens: one JSON object per line.
    pub fn recorded_opens(&self) -> Vec<serde_json::Value> {
        let content = std::fs::read_to_string(&self.action_log).unwrap_or_default();
        content
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect()
    }
}
