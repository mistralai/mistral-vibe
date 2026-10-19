//! `vibe -p --agent-socket PATH` (experimental, hidden): the app-server connects to the caller's agent server.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::cli::Cli;
use crate::run_export::RunOutcome;
use crate::server::{notification, Notification, RpcError};

/// The `session/start` error field that names how a failed handshake ends the run.
const OUTCOME_ERROR_FIELD: &str = "agentSocketOutcome";

/// The usage error of a `--agent-socket` invocation, if any.
pub fn usage_error(cli: &Cli, windows: bool) -> Option<String> {
    cli.agent_socket.as_ref()?;
    if !cli.is_headless() {
        return Some("--agent-socket needs programmatic mode: pass -p or --prompt-file".into());
    }
    if windows {
        return Some("--agent-socket needs Unix domain sockets; not supported on Windows".into());
    }
    let conflicts: Vec<&str> = [
        (workdir_flag(cli), "--workdir"),
        (cli.worktree.is_some(), "--worktree"),
        (!cli.add_dir.is_empty(), "--add-dir"),
        (cli.continue_session, "--continue"),
        (cli.teleport, "--teleport"),
    ]
    .into_iter()
    .filter_map(|(set, flag)| set.then_some(flag))
    .collect();
    (!conflicts.is_empty()).then(|| {
        format!(
            "--agent-socket cannot be combined with {}",
            conflicts.join(", ")
        )
    })
}

/// `--workdir` itself, not the `VIBE_CWD` variable that sets the same field.
fn workdir_flag(cli: &Cli) -> bool {
    cli.cwd.is_some() && std::env::var_os("VIBE_CWD").map(PathBuf::from) != cli.cwd
}

/// The app-server arguments that hand it the socket: the process that runs the tools connects.
pub fn app_server_args(path: &Path) -> [String; 2] {
    ["--agent-socket".into(), path.display().to_string()]
}

/// The socket path the app-server opens: left relative, which keeps it under the Unix socket
/// path limit, unless the app-server runs in another directory than the caller.
pub fn server_path(
    path: &Path,
    invocation_cwd: Option<&Path>,
    server_cwd: Option<&Path>,
) -> PathBuf {
    let path = crate::utils::paths::expand_tilde(path);
    match invocation_cwd {
        Some(cwd) if path.is_relative() && server_cwd.is_some_and(|server| server != cwd) => {
            cwd.join(path)
        }
        _ => path,
    }
}

/// The outcome a session open error names for a failed handshake.
pub fn named_outcome(error: &RpcError) -> Option<RunOutcome> {
    let outcome = error.data.as_ref()?.get(OUTCOME_ERROR_FIELD)?;
    serde_json::from_value(Value::clone(outcome)).ok()
}

/// The server's message, when one of `notifications` says it aborted the run.
pub fn abort_message(notifications: &[Notification]) -> Option<String> {
    notifications
        .iter()
        .find(|n| n.method == notification::AGENT_SOCKET_ABORTED)
        .map(|n| {
            let message = n.params.get("message").and_then(Value::as_str);
            message.unwrap_or_default().to_owned()
        })
}

/// Whether the session runs in the agent server's sandbox rather than in `cwd` on this host.
pub fn sandboxed(session_cwd: Option<&str>, cwd: Option<&str>) -> bool {
    let canonical = |path: &str| std::fs::canonicalize(path).ok();
    match (session_cwd, cwd) {
        (Some(session_cwd), Some(cwd)) => canonical(session_cwd) != canonical(cwd),
        _ => false,
    }
}
