//! The app-server's stderr goes to the file log, never to the terminal. Its own
//! test binary, since `init_file_logging` installs a global subscriber.

use std::path::PathBuf;

use vibe_rs::observability::logging::init_file_logging;
use vibe_rs::server::stderr::drain;

const MAX_LINE: usize = 4 * 1024;

fn log_path() -> PathBuf {
    std::env::temp_dir().join(format!("vibe-rs-stderr-{}.log", std::process::id()))
}

#[tokio::test]
async fn writes_child_stderr_to_the_log_file() {
    std::env::remove_var("LOG_LEVEL");
    std::env::remove_var("DEBUG_MODE");

    let path = log_path();
    let _ = std::fs::remove_file(&path);
    init_file_logging(Some(&path));

    // Invalid bytes must not end the drain, and the record past the cap is
    // clipped without ever being buffered whole.
    let mut input = b"boom\n    indented\r\n\n   \n\xff\xfe\n".to_vec();
    input.extend(std::iter::repeat_n(b'x', 8 * MAX_LINE));
    input.push(b'\n');
    drain(&input[..]).await;

    let body = std::fs::read_to_string(&path).unwrap();
    let lines: Vec<&str> = body.lines().collect();
    assert_eq!(lines.len(), 4, "unexpected lines: {lines:?}");
    assert!(lines[0].ends_with("boom"), "{}", lines[0]);
    assert!(lines[1].ends_with("    indented"), "{}", lines[1]);

    let clipped = lines[3].rsplit_once("app_server: ").unwrap().1;
    assert_eq!(clipped, format!("{}...", "x".repeat(MAX_LINE)));

    let _ = std::fs::remove_file(&path);
}
