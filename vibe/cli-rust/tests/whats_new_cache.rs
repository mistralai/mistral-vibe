//! The what's-new gate's first-run seeding (Python's update checks play this role).

use vibe_rs::utils::whats_new_cache;

#[test]
fn absent_section_seeds_current_without_showing() {
    let home = std::env::temp_dir().join(format!("vibe-rs-whatsnew-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&home);
    std::fs::create_dir_all(&home).unwrap();
    std::env::set_var("VIBE_HOME", &home);

    // First run: no section, so the current version is stamped without a show.
    assert!(!whats_new_cache::should_show("1.2.3"));
    let cache = std::fs::read_to_string(home.join("cache.toml")).unwrap();
    assert!(cache.contains("[whats_new]"));
    assert!(cache.contains("seen_version = \"1.2.3\""));

    // The stamped version keeps the gate closed until the version changes.
    assert!(!whats_new_cache::should_show("1.2.3"));
    assert!(whats_new_cache::should_show("1.3.0"));
    whats_new_cache::mark_seen("1.3.0");
    assert!(!whats_new_cache::should_show("1.3.0"));

    let _ = std::fs::remove_dir_all(&home);
}
