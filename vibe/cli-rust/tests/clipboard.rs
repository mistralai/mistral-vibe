//! Clipboard transport stays available over SSH on every platform.

use std::process::Command;

#[test]
#[ignore]
fn helper_copy() {
    let text = std::env::var("VIBE_TEST_CLIPBOARD_TEXT").expect("clipboard text");
    assert!(!vibe_rs::clipboard::copy_to_clipboard(&text));
}

fn copied_output(text: &str, ssh_variable: &str, tmux: bool) -> String {
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args(["--ignored", "--exact", "helper_copy", "--nocapture"])
        .env("VIBE_TEST_CLIPBOARD_TEXT", text)
        .env_remove("SSH_CONNECTION")
        .env_remove("SSH_TTY")
        .env_remove("TMUX")
        .env(ssh_variable, "test-ssh-session");
    if tmux {
        command.env("TMUX", "test-tmux-session");
    }
    let output = command.output().expect("run clipboard helper");
    assert!(output.status.success(), "{output:?}");
    String::from_utf8(output.stdout).unwrap()
}

#[test]
fn ssh_copy_emits_osc52_with_utf8_and_padding() {
    for ssh_variable in ["SSH_CONNECTION", "SSH_TTY"] {
        for (text, encoded) in [("a", "YQ=="), ("ab", "YWI="), ("café\r\n", "Y2Fmw6kNCg==")] {
            let output = copied_output(text, ssh_variable, false);
            assert!(output.contains(&format!("\x1b]52;c;{encoded}\x07")));
            assert!(!output.contains("\x1bPtmux;"));
        }
    }
}

#[test]
fn tmux_copy_wraps_osc52_in_passthrough() {
    let output = copied_output("hello", "SSH_CONNECTION", true);
    assert!(output.contains("\x1bPtmux;\x1b\x1b]52;c;aGVsbG8=\x07\x1b\\"));
}

#[test]
fn empty_copy_does_not_clear_the_clipboard() {
    let output = copied_output("", "SSH_CONNECTION", false);
    assert!(!output.contains("\x1b]52;"));
}
