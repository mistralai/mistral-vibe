//! Inside tmux, copies go through `tmux load-buffer -w`, falling back to OSC 52 passthrough.
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::Command;

use vibe_rs::clipboard::{Clipboard, SystemClipboard};

const PASSTHROUGH: &str = "\x1bPtmux;\x1b\x1b]52;c;aGVsbG8=\x07\x1b\\";

#[test]
#[ignore]
fn helper_tmux_copy() {
    assert!(!SystemClipboard.copy("hello"));
}

/// Runs a copy inside a fake tmux whose subcommands answer with `answers`; returns stdout and the tmux call log.
fn copy_in_tmux(answers: &[(&str, String)]) -> (String, String) {
    let bin = tempfile::tempdir().expect("fake tmux dir");
    let log = bin.path().join("log");
    let cases: String = answers
        .iter()
        .map(|(command, body)| format!("  {command}) {body} ;;\n"))
        .collect();
    let script = format!(
        "#!/bin/sh\necho \"$@\" >> '{}'\ncase \"$1\" in\n{cases}esac\n",
        log.display()
    );
    write_script(&bin.path().join("tmux"), &script);
    let output = Command::new(std::env::current_exe().unwrap())
        .args([
            "--ignored",
            "--exact",
            &test_name!("helper_tmux_copy"),
            "--nocapture",
        ])
        .env("PATH", bin.path())
        .env("SSH_TTY", "test-ssh-session")
        .env("TMUX", "/tmp/tmux-test/default,1,0")
        .env("TMUX_PANE", "%3")
        .output()
        .expect("run clipboard helper");
    assert!(output.status.success(), "{output:?}");
    let calls = std::fs::read_to_string(log).unwrap_or_default();
    (String::from_utf8(output.stdout).unwrap(), calls)
}

fn write_script(path: &Path, body: &str) {
    std::fs::write(path, body).expect("write fake tmux");
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755))
        .expect("make fake tmux executable");
}

fn forwarding_tmux(load_buffer: &str) -> Vec<(&'static str, String)> {
    vec![
        (
            "show-options",
            clipboard_and_clients("external", "5 /dev/pts/1\\n42 /dev/pts/7\\n9 /dev/pts/2\\n"),
        ),
        ("show-messages", "echo '193: Ms: (string) x'".to_owned()),
        ("load-buffer", load_buffer.to_owned()),
    ]
}

/// Answer to the single `show-options ... ; list-clients ...` call.
fn clipboard_and_clients(clipboard: &str, clients: &str) -> String {
    format!("printf '{clipboard}\\n{clients}'")
}

#[test]
fn copy_loads_a_tmux_buffer_for_the_most_active_client_instead_of_osc52() {
    let dir = tempfile::tempdir().expect("copied dir");
    let copied = dir.path().join("copied");
    let answers = forwarding_tmux(&format!("/bin/cat > '{}'", copied.display()));

    let (output, calls) = copy_in_tmux(&answers);

    assert_eq!(std::fs::read_to_string(copied).unwrap(), "hello");
    assert!(
        calls.contains("show-options -gv set-clipboard ; list-clients -t %3 -F"),
        "{calls}"
    );
    assert!(
        calls.contains("show-messages -T -t /dev/pts/7\n"),
        "{calls}"
    );
    assert!(
        calls.contains("load-buffer -w -t /dev/pts/7 -\n"),
        "{calls}"
    );
    assert!(!output.contains("\x1b]52;"), "{output:?}");
}

#[test]
fn copy_falls_back_to_osc52_passthrough_when_tmux_cannot_forward() {
    let mut disabled = forwarding_tmux("true");
    disabled[0].1 = clipboard_and_clients("off", "42 /dev/pts/7\\n");
    let mut no_capability = forwarding_tmux("true");
    no_capability[1].1 = "echo '193: Ms: [missing]'".to_owned();
    let mut no_client = forwarding_tmux("true");
    no_client[0].1 = clipboard_and_clients("external", "");
    let failing_load = forwarding_tmux("exit 1");

    for answers in [disabled, no_capability, no_client] {
        let (output, calls) = copy_in_tmux(&answers);
        assert!(output.contains(PASSTHROUGH), "{output:?}");
        assert!(!calls.contains("load-buffer"), "{calls}");
    }
    let (output, _) = copy_in_tmux(&failing_load);
    assert!(output.contains(PASSTHROUGH), "{output:?}");
}
