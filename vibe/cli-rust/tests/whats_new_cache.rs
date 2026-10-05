//! The what's-new gate's Python semantics over the shared `[update_cache]` section.

use std::sync::Mutex;

use vibe_rs::utils::whats_new_cache;

/// Guards VIBE_HOME, which is process-global across this binary's tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

struct TempHome(std::path::PathBuf);

impl TempHome {
    fn new(label: &str) -> Self {
        let home =
            std::env::temp_dir().join(format!("vibe-rs-whatsnew-{label}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).unwrap();
        std::env::set_var("VIBE_HOME", &home);
        Self(home)
    }

    fn cache_toml(&self) -> String {
        std::fs::read_to_string(self.0.join("cache.toml")).unwrap_or_default()
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        std::env::remove_var("VIBE_HOME");
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn no_cache_shows_nothing_and_stamps_nothing() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("missing");

    assert!(!whats_new_cache::should_show("1.2.3"));
    // Python's gate never seeds the cache; its update checks do.
    assert!(!home.cache_toml().contains("[update_cache]"));

    // A section that misses the required fields reads as no cache too.
    std::fs::write(
        home.0.join("cache.toml"),
        "[update_cache]\nseen_whats_new_version = \"1.2.3\"\n",
    )
    .unwrap();
    assert!(!whats_new_cache::should_show("1.2.3"));
}

#[test]
fn a_cache_shows_until_the_version_is_marked_seen() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("seen");
    std::fs::write(
        home.0.join("cache.toml"),
        "[update_cache]\nlatest_version = \"1.3.0\"\nstored_at_timestamp = 50\n\
         seen_whats_new_version = \"1.2.3\"\ndismissed_version = \"1.3.0\"\n",
    )
    .unwrap();

    // The stored seen version differs from the current one: show.
    assert!(whats_new_cache::should_show("2.25.6"));
    // The seen version itself does not show again.
    assert!(!whats_new_cache::should_show("1.2.3"));

    whats_new_cache::mark_seen("2.25.6");
    assert!(!whats_new_cache::should_show("2.25.6"));
    assert!(whats_new_cache::should_show("2.26.0"));

    // The stamp merged into the existing section, keeping its other fields.
    let text = home.cache_toml();
    assert!(text.contains("latest_version = \"1.3.0\""));
    assert!(text.contains("stored_at_timestamp = 50"));
    assert!(text.contains("seen_whats_new_version = \"2.25.6\""));
    assert!(text.contains("dismissed_version = \"1.3.0\""));
}

#[test]
fn a_legacy_whats_new_seen_version_keeps_the_gate_closed() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("legacy-seen");

    // A check seeded the shared section but the seen version only exists in
    // the section previous Rust-only builds wrote. It still counts as seen.
    std::fs::write(
        home.0.join("cache.toml"),
        "[update_cache]\nlatest_version = \"1.2.3\"\nstored_at_timestamp = 50\n\
         [whats_new]\nseen_version = \"1.2.3\"\n",
    )
    .unwrap();
    assert!(!whats_new_cache::should_show("1.2.3"));
    assert!(whats_new_cache::should_show("2.0.0"));

    // Marking seen persists the adopted value into the shared section.
    whats_new_cache::mark_seen("2.0.0");
    assert!(home
        .cache_toml()
        .contains("seen_whats_new_version = \"2.0.0\""));
}

#[test]
fn marking_seen_without_a_cache_creates_one() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("create");

    whats_new_cache::mark_seen("1.2.3");

    let text = home.cache_toml();
    assert!(text.contains("latest_version = \"1.2.3\""));
    assert!(text.contains("seen_whats_new_version = \"1.2.3\""));
    assert!(text.contains("stored_at_timestamp = "));
    // The created cache closes the gate on the current version.
    assert!(!whats_new_cache::should_show("1.2.3"));
}
