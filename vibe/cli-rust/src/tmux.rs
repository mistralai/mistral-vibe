//! tmux queries: the clipboard's commands and the session's `mouse` option.

use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::time::Duration;

/// Bound on the `mouse` probe, which runs before the first frame.
const PROBE_TIMEOUT: Duration = Duration::from_secs(1);

/// Whether the containing tmux session had `mouse off` at the last `refresh_mouse`.
static MOUSE_OFF: AtomicBool = AtomicBool::new(false);

/// Stdout of `tmux <args>`; `None` when tmux is missing or fails.
pub fn run(args: &[&str]) -> Option<String> {
    let output = Command::new("tmux")
        .args(args)
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8(output.stdout).ok())?
}

/// Re-read this pane's tmux `mouse` option; outside tmux, or when tmux is slow or unreadable, the mouse stays on.
pub fn refresh_mouse() {
    MOUSE_OFF.store(probe_mouse_off(), Ordering::Relaxed);
}

pub fn mouse_off() -> bool {
    MOUSE_OFF.load(Ordering::Relaxed)
}

fn probe_mouse_off() -> bool {
    let pane = std::env::var("TMUX_PANE").ok();
    if std::env::var_os("TMUX").is_none() && pane.is_none() {
        return false;
    }
    let mut args = vec!["display-message".to_owned(), "-p".to_owned()];
    if let Some(pane) = pane {
        args.extend(["-t".to_owned(), pane]);
    }
    args.push("#{mouse}".to_owned());
    let (sender, receiver) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        let _ = sender.send(run(&args));
    });
    receiver
        .recv_timeout(PROBE_TIMEOUT)
        .ok()
        .flatten()
        .is_some_and(|mouse| matches!(mouse.trim(), "0" | "off"))
}
