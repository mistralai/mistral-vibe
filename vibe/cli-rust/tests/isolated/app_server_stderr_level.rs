//! Child diagnostics survive the Rust severity threshold: the app-server
//! filters its own stderr, and stderr is no longer inherited. Its own test
//! binary, since `init_file_logging` installs a global subscriber.

use std::path::PathBuf;

use vibe_rs::observability::level;
use vibe_rs::observability::logging::init_file_logging;
use vibe_rs::server::stderr::drain;

fn log_path() -> PathBuf {
    std::env::temp_dir().join(format!("vibe-rs-stderr-level-{}.log", std::process::id()))
}

#[tokio::test]
async fn logs_child_stderr_under_a_higher_threshold() {
    std::env::remove_var("DEBUG_MODE");
    std::env::set_var("LOG_LEVEL", "ERROR");

    let path = log_path();
    let _ = std::fs::remove_file(&path);
    init_file_logging(Some(&path));
    assert_eq!(level::get_log_level_chain().effective, "ERROR");

    tracing::warn!("a rust warning stays filtered");
    drain(&b"Traceback (most recent call last):\n"[..]).await;

    let body = std::fs::read_to_string(&path).unwrap();
    let lines: Vec<&str> = body.lines().collect();
    assert_eq!(lines.len(), 1, "unexpected lines: {lines:?}");
    assert!(
        lines[0].ends_with("Traceback (most recent call last):"),
        "{}",
        lines[0]
    );

    std::env::remove_var("LOG_LEVEL");
    let _ = std::fs::remove_file(&path);
}
