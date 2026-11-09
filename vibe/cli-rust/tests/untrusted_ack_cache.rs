//! The untrusted-config warning names each ignored folder once, across launches.

use serde_json::json;
use vibe_rs::startup::banners::untrusted_warning;

/// Each test owns this process's VIBE_HOME; the lock keeps threaded runs apart.
static CACHE_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Runs `check` against a fresh `cache.toml` under a temporary VIBE_HOME.
fn with_isolated_cache(name: &str, check: impl FnOnce()) {
    let _lock = CACHE_LOCK.lock().unwrap_or_else(|err| err.into_inner());
    let home = std::env::temp_dir().join(format!(
        "vibe-rs-untrusted-ack-{}-{name}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&home);
    std::fs::create_dir_all(&home).unwrap();
    std::env::set_var("VIBE_HOME", &home);
    check();
    let _ = std::fs::remove_dir_all(&home);
}

fn untrusted(dirs: &[&str]) -> Option<String> {
    untrusted_warning(&json!({"dirs": dirs, "settingsPath": "/home/.vibe/trusted_folders.toml"}))
}

#[test]
fn an_acknowledged_folder_is_not_warned_about_on_the_next_launch() {
    with_isolated_cache("suppressed", || {
        assert!(untrusted(&["/proj/.vibe"]).is_some_and(|text| text.contains("/proj/.vibe")));
        assert_eq!(untrusted(&["/proj/.vibe"]), None);
    });
}

#[test]
fn a_newly_untrusted_folder_brings_the_warning_back() {
    with_isolated_cache("new-folder", || {
        assert!(untrusted(&["/proj/.vibe"]).is_some());
        let warning = untrusted(&["/proj/.vibe", "/proj/.agents"]).expect("a new folder warns");
        assert!(warning.contains("/proj/.agents"));
        assert!(warning.contains("/proj/.vibe"));
        assert_eq!(untrusted(&["/proj/.vibe", "/proj/.agents"]), None);
    });
}

#[test]
fn acknowledging_another_project_keeps_the_first_project_silent() {
    with_isolated_cache("other-project", || {
        assert!(untrusted(&["/proj/.vibe"]).is_some());
        assert!(untrusted(&["/other/.vibe"]).is_some_and(|text| !text.contains("/proj/.vibe")));
        assert_eq!(untrusted(&["/proj/.vibe"]), None);
        assert_eq!(untrusted(&["/other/.vibe"]), None);
    });
}
