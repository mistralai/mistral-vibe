//! Clipboard I/O: the native platform tool with an OSC 52 fallback.

use std::io::Write;
use std::process::{Command, Stdio};
use std::sync::{Arc, OnceLock};

/// Hint shown when the native copy is unverified.
pub const NATIVE_COPY_HINT: &str =
    "if paste fails, Shift+select (Option in iTerm2, Fn in Terminal.app)";
/// Local Linux/BSD hint: VTE terminals ignore OSC 52, so suggest native selection or a clipboard tool.
pub const LINUX_COPY_HINT: &str = "if paste fails, Shift+select or install xclip";

/// Clipboard port: the only side-effecting clipboard surface.
pub trait Clipboard: Send + Sync {
    /// Copy `text` to the clipboard, returning whether the native copy was verified.
    fn copy(&self, text: &str) -> bool;

    /// Hint appended to the notice of an unverified copy.
    fn unverified_hint(&self) -> &'static str {
        NATIVE_COPY_HINT
    }
}

/// Production adapter: native tool plus OSC 52 fallback.
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

    fn unverified_hint(&self) -> &'static str {
        if cfg!(not(any(target_os = "macos", target_os = "windows"))) && !is_ssh_session() {
            LINUX_COPY_HINT
        } else {
            NATIVE_COPY_HINT
        }
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

/// Hint for an unverified copy from the installed sink, the generic one without a sink.
pub fn unverified_hint() -> &'static str {
    SINK.get()
        .map_or(NATIVE_COPY_HINT, |sink| sink.unverified_hint())
}

/// Skip the native tool over SSH so OSC 52 carries the copy.
fn is_ssh_session() -> bool {
    std::env::var_os("SSH_CONNECTION").is_some() || std::env::var_os("SSH_TTY").is_some()
}

/// Pipe `text` into the first platform clipboard tool that succeeds (pbcopy/wl-copy/xclip/xsel/clip).
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
        let succeeded = child.wait().is_ok_and(|status| status.success());
        if wrote && succeeded {
            return true;
        }
        // e.g. wl-copy outside Wayland: fall through to the X11 tools
    }
    false
}

/// Copy `text` via OSC 52 on stdout, tmux-passthrough wrapped.
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
