//! Piped-prompt reading: bounded, mirroring Python `get_prompt_from_stdin`.

use std::io::IsTerminal;

use tokio::io::AsyncReadExt;

/// ADR 0016: every read is bounded. 1 MiB is far past any sane prompt, and the
/// cap keeps a hostile pipe from exhausting memory before the app-server starts.
pub const MAX_STDIN_PROMPT_BYTES: usize = 1024 * 1024;

/// What the piped bytes decode to.
pub enum PipedPrompt {
    Prompt(String),
    TooLarge,
    InvalidUtf8,
    Empty,
}

/// Cap on raw bytes: a cutoff splitting a multibyte char still reports the limit.
pub fn parse_piped_prompt(bytes: Vec<u8>) -> PipedPrompt {
    if bytes.len() > MAX_STDIN_PROMPT_BYTES {
        return PipedPrompt::TooLarge;
    }
    let Ok(text) = String::from_utf8(bytes) else {
        return PipedPrompt::InvalidUtf8;
    };
    let trimmed = text.trim();
    if trimmed.is_empty() {
        PipedPrompt::Empty
    } else {
        PipedPrompt::Prompt(trimmed.to_owned())
    }
}

/// Read stdin if it is piped (not a tty), mirroring `get_prompt_from_stdin`.
pub async fn read_stdin_prompt() -> Option<String> {
    if std::io::stdin().is_terminal() {
        return None;
    }
    let mut buf = Vec::new();
    let mut stdin = tokio::io::stdin().take((MAX_STDIN_PROMPT_BYTES + 1) as u64);
    if stdin.read_to_end(&mut buf).await.is_err() {
        return None;
    }
    // Safe to exit here: no session exists yet, so ADR 0009 cleanup is moot.
    match parse_piped_prompt(buf) {
        PipedPrompt::Prompt(prompt) => {
            reattach_controlling_tty();
            Some(prompt)
        }
        PipedPrompt::TooLarge => {
            eprintln!("Error: piped prompt exceeds {MAX_STDIN_PROMPT_BYTES} bytes");
            std::process::exit(1);
        }
        PipedPrompt::InvalidUtf8 => {
            eprintln!("Error: piped prompt is not valid UTF-8");
            std::process::exit(1);
        }
        PipedPrompt::Empty => None,
    }
}

/// Point stdin back at the terminal after a piped prompt drained it, so the TUI reads keys (Python `sys.stdin = open("/dev/tty")`).
#[cfg(unix)]
fn reattach_controlling_tty() {
    use std::io::IsTerminal;
    // Prefer the inherited terminal fds over a fresh `/dev/tty` open: crossterm
    // reads fd 0 whenever `isatty(0)` is true, and a fresh `/dev/tty` open in a
    // session whose stdin was a pipe misbehaves (reads block or error despite
    // `O_NONBLOCK`), sending crossterm's mio read loop into a permanent spin
    // before the first draw (VIBE-4746). The descriptions the terminal itself
    // opened (fd 1/2) behave, so clone one of those.
    if std::io::stdout().is_terminal() {
        // SAFETY: dup2 onto fd 0 with fd 1 open; fd 0 stays valid afterwards.
        unsafe { libc::dup2(1, 0) };
        return;
    }
    if std::io::stderr().is_terminal() {
        // SAFETY: dup2 onto fd 0 with fd 2 open; fd 0 stays valid afterwards.
        unsafe { libc::dup2(2, 0) };
        return;
    }
    // Last resort when neither std stream is the terminal, mirroring Python.
    let tty = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/tty");
    if let Ok(tty) = tty {
        use std::os::unix::io::AsRawFd;
        // SAFETY: dup2 with valid fds; fd 0 stays open after `tty` drops.
        unsafe {
            libc::dup2(tty.as_raw_fd(), libc::STDIN_FILENO);
        }
    }
}

#[cfg(not(unix))]
fn reattach_controlling_tty() {}
