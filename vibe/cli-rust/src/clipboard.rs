//! Clipboard I/O, mirroring Python's `vibe/cli/clipboard.py`.

use std::io::Write;
use std::process::{Command, Stdio};

/// Python `_is_ssh_session`: skip the native tool over SSH so OSC 52 carries the copy.
fn is_ssh_session() -> bool {
    std::env::var_os("SSH_CONNECTION").is_some() || std::env::var_os("SSH_TTY").is_some()
}

/// Copy `text` to the clipboard, returning whether the native copy was verified.
/// Mirrors Python's `copy_to_clipboard`: skip native over SSH, then always emit
/// OSC 52 as the terminal/SSH fallback.
pub fn copy_to_clipboard(text: &str) -> bool {
    if text.is_empty() {
        return false;
    }
    let verified = if is_ssh_session() {
        false
    } else {
        copy_native(text)
    };
    copy_osc52(text);
    verified
}

/// Pipe `text` into the platform clipboard tool (pbcopy / wl-copy / xclip / xsel
/// / clip), the way Python's pyperclip does. Returns whether a tool accepted the
/// write. Best-effort verification: unlike Python it does not paste back to
/// round-trip, so a spawned+written tool counts as verified.
fn copy_native(text: &str) -> bool {
    let candidates: &[(&str, &[&str])] = if cfg!(target_os = "macos") {
        &[("pbcopy", &[])]
    } else if cfg!(target_os = "windows") {
        &[("clip", &[])]
    } else {
        &[
            ("wl-copy", &[]),
            ("xclip", &["-selection", "clipboard"]),
            ("xsel", &["--clipboard", "--input"]),
        ]
    };
    for (program, args) in candidates {
        let Ok(mut child) = Command::new(program)
            .args(*args)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        else {
            continue; // tool not installed; try the next one
        };
        let wrote = child
            .stdin
            .take()
            .map(|mut stdin| stdin.write_all(text.as_bytes()).is_ok())
            .unwrap_or(false);
        let status = child.wait();
        return wrote && status.map(|s| s.success()).unwrap_or(false);
    }
    false
}

/// Copy `text` via OSC 52. The TUI owns stdout as its terminal transport, so
/// writing through the same descriptor also works in PTYs without relying on a
/// separately reopenable `/dev/tty`.
/// Wrapped in tmux passthrough when running inside tmux, matching Python.
#[cfg(unix)]
fn copy_osc52(text: &str) {
    if text.is_empty() {
        return;
    }
    let mut seq = format!("\x1b]52;c;{}\x07", base64(text.as_bytes()));
    if std::env::var_os("TMUX").is_some() {
        seq = format!("\x1bPtmux;\x1b{seq}\x1b\\");
    }
    let stdout = std::io::stdout();
    let mut terminal = stdout.lock();
    let _ = terminal.write_all(seq.as_bytes());
    let _ = terminal.flush();
}

#[cfg(not(unix))]
fn copy_osc52(_text: &str) {}

/// Minimal standard base64 with padding; avoids pulling in a crate for one use.
fn base64(input: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let n = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 {
            T[((n >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            T[(n & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}
