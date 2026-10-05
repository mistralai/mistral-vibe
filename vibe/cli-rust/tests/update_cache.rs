//! The update cache repository's validation, round-trip, and legacy migration.

use std::path::PathBuf;
use std::sync::Mutex;

use vibe_rs::update_notifier::cache::{
    FileSystemUpdateCacheRepository, UpdateCache, UpdateCacheRepository,
};

/// Guards VIBE_HOME, which is process-global across this binary's tests.
static ENV_LOCK: Mutex<()> = Mutex::new(());

struct TempHome(PathBuf);

impl TempHome {
    fn new(label: &str) -> Self {
        let home = std::env::temp_dir().join(format!(
            "vibe-rs-update-cache-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).unwrap();
        std::env::set_var("VIBE_HOME", &home);
        Self(home)
    }

    fn cache_toml(&self) -> String {
        std::fs::read_to_string(self.0.join("cache.toml")).unwrap_or_default()
    }

    fn write_cache_toml(&self, text: &str) {
        std::fs::write(self.0.join("cache.toml"), text).unwrap();
    }
}

impl Drop for TempHome {
    fn drop(&mut self) {
        std::env::remove_var("VIBE_HOME");
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn cache(latest: &str, stored_at: i64) -> UpdateCache {
    UpdateCache {
        latest_version: latest.to_owned(),
        stored_at_timestamp: stored_at,
        seen_whats_new_version: None,
        dismissed_version: None,
        source: None,
        source_stored_at: None,
    }
}

fn get() -> Option<UpdateCache> {
    FileSystemUpdateCacheRepository.get()
}

fn set(update_cache: &UpdateCache) -> std::io::Result<()> {
    FileSystemUpdateCacheRepository.set(update_cache)
}

fn modify(
    modify: &mut dyn FnMut(Option<UpdateCache>) -> Option<UpdateCache>,
) -> std::io::Result<()> {
    FileSystemUpdateCacheRepository.modify(modify)
}

#[test]
fn round_trip_preserves_other_sections_and_keys() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("round-trip");
    home.write_cache_toml(
        "[other]\nkeep = \"me\"\n[update_cache]\nlatest_version = \"1.0.0\"\n\
         stored_at_timestamp = 5\nextra_key = \"kept\"\n",
    );

    set(&UpdateCache {
        latest_version: "2.0.0".to_owned(),
        stored_at_timestamp: 10,
        seen_whats_new_version: Some("2.0.0".to_owned()),
        dismissed_version: Some("1.5.0".to_owned()),
        source: Some("uv".to_owned()),
        source_stored_at: Some(10),
    })
    .unwrap();

    assert_eq!(
        get(),
        Some(UpdateCache {
            latest_version: "2.0.0".to_owned(),
            stored_at_timestamp: 10,
            seen_whats_new_version: Some("2.0.0".to_owned()),
            dismissed_version: Some("1.5.0".to_owned()),
            source: Some("uv".to_owned()),
            source_stored_at: Some(10),
        })
    );
    let text = home.cache_toml();
    assert!(text.contains("[other]"));
    assert!(text.contains("keep = \"me\""));
    assert!(text.contains("extra_key = \"kept\""));
}

#[test]
fn set_merges_optional_keys_like_python() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let _home = TempHome::new("merge");

    set(&cache("2.0.0", 10)).unwrap();
    set(&UpdateCache {
        latest_version: "3.0.0".to_owned(),
        stored_at_timestamp: 20,
        seen_whats_new_version: Some("3.0.0".to_owned()),
        dismissed_version: None,
        source: None,
        source_stored_at: None,
    })
    .unwrap();
    // The seen key is not part of this payload, so the stored value survives.
    set(&cache("3.0.1", 30)).unwrap();

    let read = get().unwrap();
    assert_eq!(read.latest_version, "3.0.1");
    assert_eq!(read.stored_at_timestamp, 30);
    assert_eq!(read.seen_whats_new_version.as_deref(), Some("3.0.0"));
    assert_eq!(read.dismissed_version, None);
}

#[test]
fn missing_or_typed_wrong_required_fields_invalidate_the_cache() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("validation");

    home.write_cache_toml("[update_cache]\nstored_at_timestamp = 5\n");
    assert_eq!(get(), None);

    home.write_cache_toml("[update_cache]\nlatest_version = \"1.0.0\"\n");
    assert_eq!(get(), None);

    home.write_cache_toml("[update_cache]\nlatest_version = 1\nstored_at_timestamp = 5\n");
    assert_eq!(get(), None);

    home.write_cache_toml("[update_cache]\nlatest_version = \"1.0.0\"\nstored_at = \"5\"\n");
    assert_eq!(get(), None);

    // Non-string optional fields read as absent, not as invalidation.
    home.write_cache_toml(
        "[update_cache]\nlatest_version = \"1.0.0\"\nstored_at_timestamp = 5\n\
         seen_whats_new_version = 3\ndismissed_version = []\n",
    );
    assert_eq!(get(), Some(cache("1.0.0", 5)));
}

#[test]
fn missing_file_or_section_reads_as_none() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let _home = TempHome::new("missing");
    assert_eq!(get(), None);

    // Python's read_section is falsy for an empty section, so the legacy
    // JSON fallback still applies; without it the cache stays None.
}

#[test]
fn malformed_toml_falls_back_to_legacy_json() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("malformed");
    home.write_cache_toml("[update_cache\nbroken");
    std::fs::write(
        home.0.join("update_cache.json"),
        r#"{"latest_version": "2.0.0", "stored_at_timestamp": 99}"#,
    )
    .unwrap();

    assert_eq!(get(), Some(cache("2.0.0", 99)));
}

#[test]
fn legacy_json_migrates_into_the_toml_section() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("legacy");
    home.write_cache_toml("[other]\nkeep = 1\n");
    std::fs::write(
        home.0.join("update_cache.json"),
        r#"{"latest_version": "2.0.0", "stored_at_timestamp": 99,
            "seen_whats_new_version": "2.0.0", "dismissed_version": null,
            "unknown_field": "dropped"}"#,
    )
    .unwrap();

    assert_eq!(
        get(),
        Some(UpdateCache {
            latest_version: "2.0.0".to_owned(),
            stored_at_timestamp: 99,
            seen_whats_new_version: Some("2.0.0".to_owned()),
            dismissed_version: None,
            source: None,
            source_stored_at: None,
        })
    );

    // The migration wrote the toml section; the legacy file is now redundant.
    let text = home.cache_toml();
    assert!(text.contains("latest_version = \"2.0.0\""));
    assert!(text.contains("stored_at_timestamp = 99"));
    assert!(text.contains("seen_whats_new_version = \"2.0.0\""));
    assert!(text.contains("[other]"));
    assert!(!text.contains("unknown_field"));
}

#[test]
fn empty_section_still_falls_back_to_legacy_json() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("empty-section");
    home.write_cache_toml("[update_cache]\n");
    std::fs::write(
        home.0.join("update_cache.json"),
        r#"{"latest_version": "2.0.0", "stored_at_timestamp": 99}"#,
    )
    .unwrap();

    // Python's read_section is falsy for an empty section, so the legacy
    // migration fires through it.
    assert_eq!(get(), Some(cache("2.0.0", 99)));
}

#[test]
fn invalid_nonempty_section_suppresses_legacy_fallback() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("invalid-section");
    home.write_cache_toml("[update_cache]\nlatest_version = \"1.0.0\"\n");
    std::fs::write(
        home.0.join("update_cache.json"),
        r#"{"latest_version": "2.0.0", "stored_at_timestamp": 99}"#,
    )
    .unwrap();

    // A present-but-invalid section is authoritative: None, not the legacy file.
    assert_eq!(get(), None);
}

#[test]
fn a_write_that_cannot_persist_reports_the_failure() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("malformed-set");
    let broken = "[update_cache\nbroken";
    home.write_cache_toml(broken);

    assert!(set(&cache("2.0.0", 10)).is_err());
    assert!(modify(&mut |current| {
        current.map(|current| UpdateCache {
            latest_version: "3.0.0".to_owned(),
            stored_at_timestamp: 20,
            ..current
        })
    })
    .is_err());

    // The whole-file write is skipped instead of dropping the unparsed
    // content; Python instead rewrites from an empty parse (documented
    // divergence, shared with the existing single-key writes).
    assert_eq!(home.cache_toml(), broken);
}

#[test]
fn modify_keeps_the_seen_and_dismissed_fields() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("modify-keeps");
    home.write_cache_toml(
        "[update_cache]\nlatest_version = \"1.0.0\"\nstored_at_timestamp = 5\n\
         seen_whats_new_version = \"1.0.0\"\ndismissed_version = \"1.5.0\"\n",
    );

    modify(&mut |current| {
        Some(UpdateCache {
            latest_version: "2.0.0".to_owned(),
            stored_at_timestamp: 10,
            ..current.unwrap()
        })
    })
    .unwrap();

    assert_eq!(
        get(),
        Some(UpdateCache {
            latest_version: "2.0.0".to_owned(),
            stored_at_timestamp: 10,
            seen_whats_new_version: Some("1.0.0".to_owned()),
            dismissed_version: Some("1.5.0".to_owned()),
            source: None,
            source_stored_at: None,
        })
    );
}

#[test]
fn modify_returning_none_writes_nothing() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("modify-none");
    let seeded = "[update_cache]\nlatest_version = \"1.0.0\"\nstored_at_timestamp = 5\n";
    home.write_cache_toml(seeded);

    // Python's mark-as-dismissed no-ops without a cache: the closure sees
    // the stored one here, returns None, and the file stays untouched.
    modify(&mut |current| {
        assert_eq!(current, Some(cache("1.0.0", 5)));
        None
    })
    .unwrap();

    assert_eq!(home.cache_toml(), seeded);
}

#[test]
fn modify_migrates_legacy_json_even_without_a_change() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("modify-legacy");
    home.write_cache_toml("[other]\nkeep = 1\n");
    std::fs::write(
        home.0.join("update_cache.json"),
        r#"{"latest_version": "2.0.0", "stored_at_timestamp": 99}"#,
    )
    .unwrap();

    // A get-equivalent read migrates the legacy file even when the caller
    // makes no further change, like Python's `_read_section`.
    modify(&mut |current| {
        assert_eq!(current, Some(cache("2.0.0", 99)));
        None
    })
    .unwrap();

    let text = home.cache_toml();
    assert!(text.contains("latest_version = \"2.0.0\""));
    assert!(text.contains("stored_at_timestamp = 99"));
    assert!(text.contains("[other]"));
}

#[test]
fn invalid_legacy_json_reads_as_none() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("legacy-invalid");
    std::fs::write(home.0.join("update_cache.json"), "not json").unwrap();
    assert_eq!(get(), None);

    // A JSON payload whose required fields are invalid parses to None, but the
    // migration write still happened, mirroring Python's `_read_section`.
    std::fs::write(
        home.0.join("update_cache.json"),
        r#"{"latest_version": null}"#,
    )
    .unwrap();
    assert_eq!(get(), None);
}

#[test]
fn a_modify_persists_the_legacy_whats_new_seen_version() {
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let home = TempHome::new("legacy-seen-modify");

    // The shared section exists but has no seen version; the old Rust-only
    // section carries the one the user already dismissed.
    home.write_cache_toml(
        "[update_cache]\nlatest_version = \"1.2.3\"\nstored_at_timestamp = 50\n\
         [whats_new]\nseen_version = \"1.2.3\"\n",
    );

    // A check's read-modify-write that keeps the cache adopts the legacy
    // value, so the settled state no longer depends on the old section.
    modify(&mut |current| current).unwrap();
    let text = home.cache_toml();
    assert!(text.contains("seen_whats_new_version = \"1.2.3\""));
}
