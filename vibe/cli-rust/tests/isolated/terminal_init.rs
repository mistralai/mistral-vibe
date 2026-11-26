//! A terminal the CLI never took over must not be restored. Runs on Windows too:
//! the guard is armed after init, which is platform-independent.

use std::process::{Command, Stdio};

#[test]
fn failed_terminal_init_writes_no_teardown_sequences() {
    let home = tempfile::tempdir().unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_vibe-rs"));
    command
        .current_dir(home.path())
        .env("VIBE_HOME", home.path())
        .env("TERM", "xterm-256color")
        .env_remove("SENTRY_DSN")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // Unix keeps a controlling terminal across pipes, so detach from it; Windows
    // pipes already leave the child without a console to take over.
    #[cfg(unix)]
    unsafe {
        use std::os::unix::process::CommandExt;
        command.pre_exec(|| match libc::setsid() {
            -1 => Err(std::io::Error::last_os_error()),
            _ => Ok(()),
        });
    }
    let output = command.spawn().unwrap().wait_with_output().unwrap();
    assert!(!output.status.success(), "CLI unexpectedly started a TUI");
    let written = [output.stdout, output.stderr].concat();
    for sequence in [
        &b"\x1b[?1049l"[..], // leave alternate screen
        &b"\x1b[?1000l"[..], // disable mouse capture
        &b"\x1b[?2004l"[..], // disable bracketed paste
        &b"\x1b[<u"[..],     // pop keyboard enhancement flags
        &b"\x1b]22;"[..],    // pointer shape reset
    ] {
        assert!(
            !written
                .windows(sequence.len())
                .any(|bytes| bytes == sequence),
            "restored a terminal it never owned: {:?}",
            String::from_utf8_lossy(sequence)
        );
    }
}
