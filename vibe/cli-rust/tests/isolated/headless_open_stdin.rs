//! A headless run with `--prompt-file` must not wait for EOF on a stdin pipe the caller keeps open.

use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[test]
fn prompt_file_run_ends_while_stdin_stays_open() {
    let dir = tempfile::tempdir().unwrap();
    let prompt = dir.path().join("prompt.md");
    std::fs::write(&prompt, "say hi").unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_vibe-rs"))
        .args(["-p", "--prompt-file"])
        .arg(&prompt)
        .arg("--output-dir")
        .arg(dir.path().join("out"))
        .arg("--workdir")
        .arg(dir.path())
        .env("VIBE_HOME", dir.path().join("home"))
        .env("VIBE_APP_SERVER_BIN", dir.path().join("no-such-app-server"))
        .env_remove("VIBE_APP_SERVER_CMD")
        .env_remove("VIBE_CWD")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let _held_open = child.stdin.take();
    let started = Instant::now();
    while child.try_wait().unwrap().is_none() {
        if started.elapsed() > Duration::from_secs(20) {
            child.kill().ok();
            panic!("the run blocked on the open stdin pipe");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(dir.path().join("out/export.json").exists());
}
