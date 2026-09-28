//! End to end: the level chain gates the file sink and a runtime change takes
//! effect live. Its own test binary, since `init_file_logging` installs a
//! global subscriber.

use std::path::PathBuf;

use vibe_rs::observability::level::{self, DEFAULT_LOG_LEVEL};
use vibe_rs::observability::logging::init_file_logging;

fn log_path() -> PathBuf {
    std::env::temp_dir().join(format!("vibe-rs-log-{}.log", std::process::id()))
}

#[test]
fn writes_only_what_the_effective_level_admits() {
    // Isolated from the developer's own environment.
    std::env::remove_var("LOG_LEVEL");
    std::env::remove_var("DEBUG_MODE");

    let path = log_path();
    let _ = std::fs::remove_file(&path);
    init_file_logging(Some(&path));

    assert_eq!(level::get_log_level_chain().effective, DEFAULT_LOG_LEVEL);
    tracing::info!("below the default threshold");
    tracing::warn!("at the default threshold");

    level::set_session_override(Some("DEBUG"));
    tracing::debug!(detail = 7, "under the session override");

    level::set_session_override(Some("CRITICAL"));
    tracing::error!("silenced by CRITICAL");

    let body = std::fs::read_to_string(&path).unwrap();
    let lines: Vec<&str> = body.lines().collect();
    assert_eq!(lines.len(), 2, "unexpected lines: {lines:?}");
    assert!(lines[0].contains("WARN"), "{}", lines[0]);
    assert!(
        lines[0].ends_with("at the default threshold"),
        "{}",
        lines[0]
    );
    assert!(
        lines[1].ends_with("under the session override detail=7"),
        "{}",
        lines[1]
    );

    // The config tier only applies once the session override is gone.
    level::set_session_override(None);
    level::set_config_log_level(Some("ERROR"));
    assert_eq!(level::get_log_level_chain().effective, "ERROR");

    // One callsite, visited under three levels. Reading the level in
    // `Layer::enabled` would freeze it at the first visit, since tracing caches
    // a callsite's `Interest` forever.
    level::set_config_log_level(None);
    for level in ["WARNING", "DEBUG", "CRITICAL"] {
        level::set_session_override(Some(level));
        tracing::info!("same callsite");
    }
    let body = std::fs::read_to_string(&path).unwrap();
    let added: Vec<&str> = body.lines().skip(lines.len()).collect();
    assert_eq!(added.len(), 1, "unexpected lines: {added:?}");
    assert!(added[0].contains("INFO"), "{}", added[0]);
    assert!(added[0].ends_with("same callsite"), "{}", added[0]);

    level::set_session_override(None);
    let _ = std::fs::remove_file(&path);
}
