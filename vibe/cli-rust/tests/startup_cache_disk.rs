//! Startup first-frame cache file load and store contracts.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{json, Value};

use vibe_rs::utils::startup_cache::{StartupConfig, MAX_AGE_SECONDS};

const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

/// Guards VIBE_HOME, which is process-global across this binary's tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

struct RestoreEnv(&'static str, Option<std::ffi::OsString>);

impl Drop for RestoreEnv {
    fn drop(&mut self) {
        match self.1.take() {
            Some(value) => std::env::set_var(self.0, value),
            None => std::env::remove_var(self.0),
        }
    }
}

fn runtime() -> Value {
    serde_json::json!({
        "runtime": {
            "config": {
                "theme": "ansi-dark",
                "activeModel": {"displayName": "Medium", "thinking": "off"},
                "defaultAgent": "plan",
            },
            "agents": [
                {"name": "ask", "displayName": "Ask", "safety": "neutral"},
                {"name": "plan", "displayName": "Plan", "safety": "safe"},
            ],
        }
    })
}

fn fresh_config() -> StartupConfig {
    StartupConfig::from_runtime(CLIENT_VERSION, &runtime()).unwrap()
}

/// Creates a temp VIBE_HOME and points the process at it.
/// Callers must hold `ENV_LOCK` and an active `RestoreEnv` first.
fn isolated_home(name: &str) -> PathBuf {
    let home = std::env::temp_dir().join(format!(
        "vibe-rs-startup-cache-{}-{name}",
        std::process::id()
    ));
    std::env::set_var("VIBE_HOME", &home);
    std::fs::create_dir_all(&home).expect("create test home");
    home
}

fn cache_path(home: &Path) -> PathBuf {
    home.join("ui_startup_config.json")
}

fn with_isolated_home(name: &str, f: impl FnOnce(&Path)) {
    let _lock = ENV_LOCK.lock().unwrap();
    let _restore = RestoreEnv("VIBE_HOME", std::env::var_os("VIBE_HOME"));
    let home = isolated_home(name);
    f(&home);
    std::fs::remove_dir_all(&home).expect("remove test home");
}

#[test]
fn a_fresh_cache_file_is_loaded() {
    with_isolated_home("load-fresh", |_home| {
        let config = fresh_config();
        assert!(config.update_cache().unwrap());

        let loaded = StartupConfig::load().unwrap();
        assert_eq!(loaded, config);
        assert!(loaded.is_fresh(CLIENT_VERSION, loaded.cached_at));
    });
}

#[test]
fn a_schema_7_cache_file_without_a_stamp_is_rejected() {
    with_isolated_home("schema-7", |home| {
        let mut legacy = serde_json::to_value(fresh_config()).unwrap();
        legacy["schemaVersion"] = json!(7);
        legacy.as_object_mut().unwrap().remove("cachedAt");
        std::fs::write(cache_path(home), serde_json::to_vec(&legacy).unwrap())
            .expect("write legacy cache");

        assert!(StartupConfig::load().is_none());
    });
}

#[test]
fn a_stale_cache_file_is_rejected() {
    with_isolated_home("stale", |home| {
        let mut stale = fresh_config();
        stale.cached_at -= MAX_AGE_SECONDS + 1;
        std::fs::write(cache_path(home), serde_json::to_vec_pretty(&stale).unwrap())
            .expect("write stale cache");

        assert!(StartupConfig::load().is_none());
    });
}

#[test]
fn an_oversized_cache_file_is_ignored() {
    with_isolated_home("oversized", |home| {
        let mut body = serde_json::to_vec_pretty(&fresh_config()).unwrap();
        body.resize(1024 * 1024 + 1, b' ');
        std::fs::write(cache_path(home), body).expect("write oversized cache");

        assert!(StartupConfig::load().is_none());
    });
}

#[test]
fn update_cache_skips_the_write_when_the_cache_is_unchanged() {
    with_isolated_home("skip-rewrite", |_home| {
        let config = fresh_config();
        assert!(config.update_cache().unwrap());
        assert!(!config.update_cache().unwrap());
    });
}

#[test]
fn update_cache_skips_the_write_when_only_the_stamp_changed() {
    with_isolated_home("skip-stamp-rewrite", |home| {
        let config = fresh_config();
        assert!(config.update_cache().unwrap());

        let mut newer = config.clone();
        newer.cached_at += 1;
        assert!(!newer.update_cache().unwrap());
        let on_disk: StartupConfig =
            serde_json::from_slice(&std::fs::read(cache_path(home)).unwrap()).unwrap();
        assert_eq!(on_disk.cached_at, config.cached_at);
    });
}

#[test]
fn update_cache_rewrites_when_the_payload_changed() {
    with_isolated_home("rewrite-payload", |home| {
        let config = fresh_config();
        assert!(config.update_cache().unwrap());

        let mut changed = config.clone();
        changed.theme = "ansi-light".into();
        changed.cached_at += 1;
        assert!(changed.update_cache().unwrap());
        let on_disk: StartupConfig =
            serde_json::from_slice(&std::fs::read(cache_path(home)).unwrap()).unwrap();
        assert_eq!(on_disk, changed);
    });
}

#[test]
fn update_cache_rewrites_a_stale_disk_copy() {
    with_isolated_home("rewrite-stale", |_home| {
        let mut stale = fresh_config();
        stale.cached_at -= MAX_AGE_SECONDS + 1;
        assert!(stale.update_cache().unwrap());

        let fresh = fresh_config();
        assert!(fresh.update_cache().unwrap());
        assert_eq!(StartupConfig::load().unwrap(), fresh);
    });
}
