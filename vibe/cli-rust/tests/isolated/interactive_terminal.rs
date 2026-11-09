//! The TUI refuses to start without a terminal instead of drawing into a file or spinning.

use std::process::{Command, Stdio};

#[test]
fn interactive_mode_without_a_terminal_exits_with_an_error() {
    let home = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_vibe-rs"))
        .current_dir(home.path())
        .env("VIBE_HOME", home.path())
        .env_remove("SENTRY_DSN")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("Interactive mode needs a terminal"),
        "{stderr}"
    );
}
