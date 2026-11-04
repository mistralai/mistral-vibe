//! Inside a tmux session with `mouse off`, Vibe leaves the mouse to the terminal.
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use vibe_rs::terminal::captures_mouse;
use vibe_rs::tmux;

/// Put a fake `tmux` answering `body` first on PATH; returns the file logging its calls.
fn fake_tmux(dir: &Path, body: &str) -> std::path::PathBuf {
    let log = dir.join("log");
    let script = dir.join("tmux");
    std::fs::write(
        &script,
        format!("#!/bin/sh\necho \"$@\" >> '{}'\n{body}\n", log.display()),
    )
    .expect("write fake tmux");
    std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755))
        .expect("make fake tmux executable");
    std::env::set_var("PATH", dir);
    log
}

#[test]
fn tmux_mouse_off_overrides_the_capture_setting() {
    let dir = tempfile::tempdir().expect("fake tmux dir");
    std::env::set_var("TMUX", "/tmp/tmux-test/default,1,0");
    std::env::set_var("TMUX_PANE", "%3");
    let answer_file = dir.path().join("mouse");
    let log = fake_tmux(dir.path(), &format!("/bin/cat '{}'", answer_file.display()));
    // macOS scans a new executable on its first run, which can exceed the probe timeout under load.
    let _ = std::process::Command::new(dir.path().join("tmux")).output();

    for (answer, off) in [("off", true), ("0", true), ("on", false), ("1", false)] {
        std::fs::write(&answer_file, answer).expect("write tmux answer");
        tmux::refresh_mouse();
        assert_eq!(captures_mouse(true), !off, "{answer}");
        assert!(!captures_mouse(false), "{answer}");
        let calls = std::fs::read_to_string(&log).unwrap();
        assert!(
            calls.ends_with("display-message -p -t %3 #{mouse}\n"),
            "{calls}"
        );
    }
}

#[test]
fn outside_tmux_the_setting_decides_without_running_tmux() {
    let dir = tempfile::tempdir().expect("fake tmux dir");
    let log = fake_tmux(dir.path(), "echo off");
    std::env::remove_var("TMUX");
    std::env::remove_var("TMUX_PANE");

    tmux::refresh_mouse();

    assert!(captures_mouse(true));
    assert!(!captures_mouse(false));
    assert!(!log.exists());
}
