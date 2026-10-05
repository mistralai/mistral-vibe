//! A fatal error prints the Python fallback hint only on rollout launches.

use std::process::{Command, Stdio};

use vibe_rs::rollout::{FALLBACK_HINT, ROLLOUT_ENV};

fn fail_with_rollout_env(value: Option<&str>) -> String {
    let home = tempfile::tempdir().unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_vibe-rs"));
    command
        .current_dir(home.path())
        .args(["--add-dir", "missing-dir"])
        .env("VIBE_HOME", home.path())
        .env_remove("SENTRY_DSN")
        .env_remove(ROLLOUT_ENV)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(value) = value {
        command.env(ROLLOUT_ENV, value);
    }
    let output = command.output().unwrap();
    assert!(!output.status.success(), "CLI unexpectedly succeeded");
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn rollout_launch_prints_fallback_hint_on_error() {
    assert!(fail_with_rollout_env(Some("1")).contains(FALLBACK_HINT));
}

#[test]
fn non_rollout_launch_prints_no_hint_on_error() {
    assert!(!fail_with_rollout_env(None).contains(FALLBACK_HINT));
}
