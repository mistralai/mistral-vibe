//! Piped prompts hand keyboard input back to the terminal and still work headless.

use std::io::Write;
use std::process::{Command, Stdio};

#[tokio::test]
#[ignore]
async fn helper_read_prompt() {
    let expected = std::env::var("VIBE_TEST_PROMPT").unwrap();
    let prompt = vibe_rs::headless_prompt::read_stdin_prompt().await;
    assert_eq!(
        prompt.as_deref(),
        (!expected.is_empty()).then_some(expected.as_str())
    );
    if std::env::var_os("VIBE_TEST_TERMINAL").is_some() {
        use std::io::IsTerminal;
        assert!(std::io::stdin().is_terminal());
    }
}

fn read_prompt(bytes: &[u8], expected: &str) {
    run_helper(bytes, expected, Stdio::piped(), false);
}

fn run_helper(bytes: &[u8], expected: &str, stdout: Stdio, terminal: bool) {
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args(["--ignored", "--exact", "helper_read_prompt", "--nocapture"])
        .env("VIBE_TEST_PROMPT", expected)
        .env_remove("VIBE_TEST_TERMINAL")
        .stdin(Stdio::piped())
        .stdout(stdout)
        .stderr(Stdio::piped());
    if terminal {
        command.env("VIBE_TEST_TERMINAL", "1");
    }
    let mut child = command.spawn().unwrap();
    child.stdin.take().unwrap().write_all(bytes).unwrap();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while child.try_wait().unwrap().is_none() {
        if std::time::Instant::now() >= deadline {
            child.kill().unwrap();
            let output = child.wait_with_output().unwrap();
            panic!("stdin helper timed out: {output:?}");
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success(), "{output:?}");
}

#[test]
fn headless_pipe_does_not_require_a_console() {
    read_prompt(b"  hello\r\n", "hello");
}

#[test]
fn empty_and_whitespace_pipes_do_not_create_a_prompt() {
    read_prompt(b"", "");
    read_prompt(b" \r\n\t", "");
}

#[cfg(unix)]
#[test]
fn prompt_and_empty_pipes_reattach_terminal_input() {
    for (bytes, expected) in [(&b"hello\n"[..], "hello"), (b"", ""), (b" \n\t", "")] {
        let (_master, slave) = open_pty();
        run_helper(bytes, expected, Stdio::from(slave), true);
    }
}

#[cfg(unix)]
fn open_pty() -> (std::fs::File, std::fs::File) {
    use std::os::fd::FromRawFd;

    let mut master = -1;
    let mut slave = -1;
    // SAFETY: openpty writes two fresh fds; null termios/winsize use defaults.
    let opened = unsafe {
        libc::openpty(
            &mut master,
            &mut slave,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    };
    assert_eq!(opened, 0);
    for fd in [master, slave] {
        // SAFETY: fd was just opened; CLOEXEC keeps it out of the helper beyond stdout.
        assert_eq!(
            unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) },
            0
        );
    }
    // SAFETY: both fds are open and owned by nothing else.
    unsafe {
        (
            std::fs::File::from_raw_fd(master),
            std::fs::File::from_raw_fd(slave),
        )
    }
}
