//! Clipboard I/O, mirroring Python's `vibe/cli/clipboard.py`.

use std::io::Write;
use std::process::{Command, Stdio};
use std::sync::{Arc, OnceLock};

/// Clipboard port: the only side-effecting clipboard surface.
pub trait Clipboard: Send + Sync {
    /// Copy `text` to the clipboard, returning whether the native copy was verified.
    fn copy(&self, text: &str) -> bool;
}

/// Production adapter: native tool plus OSC 52 fallback (Python `copy_to_clipboard`).
pub struct SystemClipboard;

impl Clipboard for SystemClipboard {
    fn copy(&self, text: &str) -> bool {
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
}

/// Test adapter: records nothing, writes nothing.
pub struct NullClipboard;

impl Clipboard for NullClipboard {
    fn copy(&self, _text: &str) -> bool {
        false
    }
}

/// The installed sink, set once at process start (composition root).
static SINK: OnceLock<Arc<dyn Clipboard>> = OnceLock::new();

/// Install the process clipboard sink; panics if one is already installed.
pub fn set_sink(sink: Arc<dyn Clipboard>) {
    if SINK.set(sink).is_err() {
        panic!("clipboard sink installed twice");
    }
}

pub fn installed_sink() -> Option<Arc<dyn Clipboard>> {
    SINK.get().cloned()
}

/// Copy `text` through the installed sink; panics when none is installed.
pub fn copy_to_clipboard(text: &str) -> bool {
    SINK.get()
        .expect("clipboard sink installed at process start")
        .copy(text)
}

/// Python `_is_ssh_session`: skip the native tool over SSH so OSC 52 carries the copy.
fn is_ssh_session() -> bool {
    std::env::var_os("SSH_CONNECTION").is_some() || std::env::var_os("SSH_TTY").is_some()
}

/// Pipe `text` into the platform clipboard tool (pbcopy/wl-copy/xclip/xsel/clip), like Python's pyperclip.
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

/// Copy `text` via OSC 52 on stdout, tmux-passthrough wrapped, matching Python.
fn copy_osc52(text: &str) {
    use base64::Engine as _;

    if text.is_empty() {
        return;
    }
    let mut seq = format!(
        "\x1b]52;c;{}\x07",
        base64::engine::general_purpose::STANDARD.encode(text.as_bytes())
    );
    if std::env::var_os("TMUX").is_some() {
        seq = format!("\x1bPtmux;\x1b{seq}\x1b\\");
    }
    let stdout = std::io::stdout();
    let mut terminal = stdout.lock();
    let _ = terminal.write_all(seq.as_bytes());
    let _ = terminal.flush();
}
