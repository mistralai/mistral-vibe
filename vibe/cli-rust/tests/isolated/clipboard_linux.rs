//! Local Linux copy falls through failing native tools and hints at installing one.
#![cfg(target_os = "linux")]

use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::Command;

use vibe_rs::clipboard::{Clipboard, SystemClipboard, LINUX_COPY_HINT, NATIVE_COPY_HINT};

#[test]
#[ignore]
fn helper_native_copy() {
    let text = std::env::var("VIBE_TEST_CLIPBOARD_TEXT").expect("clipboard text");
    let verified = SystemClipboard.copy(&text);
    println!(
        "verified={verified} hint={}",
        SystemClipboard.unverified_hint()
    );
}

fn write_tool(dir: &Path, name: &str, body: &str) {
    let path = dir.join(name);
    std::fs::write(&path, format!("#!/bin/sh\n{body}\n")).expect("write fake tool");
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755))
        .expect("make fake tool executable");
}

fn copy_with_tools_in(bin: &Path, text: &str, ssh: bool) -> String {
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args([
            "--ignored",
            "--exact",
            &test_name!("helper_native_copy"),
            "--nocapture",
        ])
        .env("PATH", bin)
        .env("VIBE_TEST_CLIPBOARD_TEXT", text)
        .env_remove("SSH_CONNECTION")
        .env_remove("SSH_TTY")
        .env_remove("TMUX");
    if ssh {
        command.env("SSH_TTY", "test-ssh-session");
    }
    let output = command.output().expect("run clipboard helper");
    assert!(output.status.success(), "{output:?}");
    String::from_utf8(output.stdout).unwrap()
}

#[test]
fn a_failing_wayland_tool_falls_through_to_xclip() {
    let bin = tempfile::tempdir().expect("fake tool dir");
    let copied = bin.path().join("copied");
    let args = bin.path().join("args");
    write_tool(bin.path(), "wl-copy", "exit 1");
    write_tool(
        bin.path(),
        "xclip",
        &format!(
            "echo \"$@\" > '{}'\n/bin/cat > '{}'",
            args.display(),
            copied.display()
        ),
    );

    let output = copy_with_tools_in(bin.path(), "hello", false);

    assert!(output.contains("verified=true"), "{output}");
    assert_eq!(std::fs::read_to_string(copied).unwrap(), "hello");
    assert_eq!(
        std::fs::read_to_string(args).unwrap(),
        "-selection clipboard\n"
    );
}

#[test]
fn failing_wayland_and_xclip_tools_fall_through_to_xsel() {
    let bin = tempfile::tempdir().expect("fake tool dir");
    let copied = bin.path().join("copied");
    let args = bin.path().join("args");
    write_tool(bin.path(), "wl-copy", "exit 1");
    write_tool(bin.path(), "xclip", "exit 1");
    write_tool(
        bin.path(),
        "xsel",
        &format!(
            "echo \"$@\" > '{}'\n/bin/cat > '{}'",
            args.display(),
            copied.display()
        ),
    );

    let output = copy_with_tools_in(bin.path(), "hello", false);

    assert!(output.contains("verified=true"), "{output}");
    assert_eq!(std::fs::read_to_string(copied).unwrap(), "hello");
    assert_eq!(
        std::fs::read_to_string(args).unwrap(),
        "--clipboard --input\n"
    );
}

#[test]
fn without_a_working_native_tool_the_hint_suggests_installing_one() {
    let bin = tempfile::tempdir().expect("fake tool dir");
    for tool in ["wl-copy", "xclip", "xsel"] {
        write_tool(bin.path(), tool, "exit 1");
    }

    let output = copy_with_tools_in(bin.path(), "hello", false);

    assert!(output.contains("verified=false"), "{output}");
    assert!(output.contains(LINUX_COPY_HINT), "{output}");
}

#[test]
fn over_ssh_the_hint_points_to_native_selection_only() {
    let bin = tempfile::tempdir().expect("fake tool dir");

    let output = copy_with_tools_in(bin.path(), "hello", true);

    assert!(output.contains("verified=false"), "{output}");
    assert!(output.contains(NATIVE_COPY_HINT), "{output}");
}
