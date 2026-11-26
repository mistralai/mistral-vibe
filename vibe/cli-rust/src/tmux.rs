//! tmux queries: the clipboard's commands, the session's `mouse` option and the attached terminal.

use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Mutex};
use std::time::Duration;

/// Bound on the startup probe, which runs before the first frame.
const PROBE_TIMEOUT: Duration = Duration::from_secs(1);

/// Whether the containing tmux session had `mouse off` at the last `refresh_mouse`.
static MOUSE_OFF: AtomicBool = AtomicBool::new(false);
/// The attached terminal's `client_termtype` at the last `refresh_mouse`.
static CLIENT_TERMTYPE: Mutex<Option<String>> = Mutex::new(None);

/// Stdout of `tmux <args>`; `None` when tmux is missing or fails.
pub fn run(args: &[&str]) -> Option<String> {
    let child = crate::job_control::spawn(
        Command::new("tmux")
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null()),
    );
    let output = child.ok()?.wait_with_output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8(output.stdout).ok())?
}

/// Re-read this pane's `mouse` option and `client_termtype`; outside tmux, or when tmux is slow or unreadable, the mouse stays on and the termtype unknown.
pub fn refresh_mouse() {
    let output = probe().unwrap_or_default();
    let mut fields = output.lines().next().unwrap_or_default().split('\t');
    let off = matches!(fields.next().map(str::trim), Some("0" | "off"));
    MOUSE_OFF.store(off, Ordering::Relaxed);
    if let Ok(mut termtype) = CLIENT_TERMTYPE.lock() {
        *termtype = fields.next().map(str::to_owned);
    }
}

pub fn mouse_off() -> bool {
    MOUSE_OFF.load(Ordering::Relaxed)
}

/// The attached terminal's XTVERSION reply, such as `iTerm2 3.7.7`; empty when it sent none (Terminal.app, or tmux before 3.2), `None` outside tmux.
pub fn client_termtype() -> Option<String> {
    CLIENT_TERMTYPE.lock().ok()?.clone()
}

fn probe() -> Option<String> {
    let pane = std::env::var("TMUX_PANE").ok();
    if std::env::var_os("TMUX").is_none() && pane.is_none() {
        return None;
    }
    let mut args = vec!["display-message".to_owned(), "-p".to_owned()];
    if let Some(pane) = pane {
        args.extend(["-t".to_owned(), pane]);
    }
    args.push("#{mouse}\t#{client_termtype}".to_owned());
    let (sender, receiver) = mpsc::sync_channel(1);
    std::thread::spawn(move || {
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        let _ = sender.send(run(&args));
    });
    receiver.recv_timeout(PROBE_TIMEOUT).ok().flatten()
}
