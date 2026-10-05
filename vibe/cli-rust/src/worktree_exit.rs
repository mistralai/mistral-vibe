//! Worktree exit cleanup: the client-side prompt sequence of Python
//! `_cleanup_worktree_on_exit`, driven over `workspace/git/worktrees/remove`.

use std::io::{IsTerminal, Write};
use std::sync::Arc;

use crossterm::event::{Event, KeyCode, KeyEventKind, KeyModifiers};

use crate::app::App;
use crate::server::signal::ShutdownSignal;
use crate::server::{method, Client, WorktreeRemoveParams, WorktreeRemoveResponse};
use crate::worktree::WorktreeInfo;

pub const YELLOW: &str = "\x1b[33m";
pub const DIM: &str = "\x1b[2m";
const RESET: &str = "\x1b[0m";

/// Exit cleanup for a worktree this run created, mirroring Python
/// `_run_cli_with_worktree_cleanup` + `_cleanup_worktree_on_exit`: only a
/// clean interactive exit prompts, and only for a worktree `--worktree`
/// asked for and Vibe created this run. Prompts stay client-side; git runs
/// server-side over `workspace/git/worktrees/remove`. Never fails the exit.
pub async fn cleanup_on_exit(app: &App, client: &Arc<Client>, shutdown: &mut ShutdownSignal) {
    if app.session.agent_config.worktree.is_none() {
        return;
    }
    // The pin is this run's announce, not the session's tracking: a resumed
    // session's restored effect carries the original run's `created` verdict,
    // and reuse must stay non-cleaning (Python `worktree_session.created`).
    let Some(info) = app.session.prepared_worktree.clone().filter(|w| w.created) else {
        return;
    };
    // Python inspects before removing, so "Removing worktree" is printed only
    // when the removal will really happen; the server's `inspect` probe is
    // that inspect, with no side effect.
    match inspect_request(client, &info.path).await {
        Some(probe) => match probe.outcome.as_str() {
            "kept_dirty" => dirty_flow(client, &info, probe, shutdown).await,
            "kept_error" => {
                print_yellow(&format!(
                    "Could not inspect worktree for cleanup: {}",
                    removal_reason(&probe)
                ));
            }
            "removed" => {
                // The plain remove never discounted the asking session's
                // holder, so the confirmed discard is the one path that
                // removes while this session still holds the worktree.
                // `delete_branch: None` keeps the server default: delete the
                // branch when Vibe created it. Like the dirty flow, the
                // progress line waits for the removal it announces, so a
                // probe-to-force holder race never walks it back.
                if let Some(response) = remove_request(client, &info.path, true, None, false).await
                {
                    if let Some(line) = forced_progress_line(&info, &response.outcome) {
                        print_dim(&line);
                    }
                    report_kept_or_removed(&info, &response);
                }
            }
            "kept_in_use" => {
                // Python reports a held worktree at inspect time too, so the
                // user learns why nothing was removed; `outcome_lines` carries
                // the other-holder count.
                for (color, text) in outcome_lines(&info, &probe) {
                    print_line(color, &text);
                }
            }
            outcome => {
                tracing::debug!(outcome, "worktree cleanup skipped after inspect");
            }
        },
        // Inspect is unavailable (older server rejecting the additive param,
        // ADR 0014, or a dead transport): without the probe there is no
        // confirmed-discard path, so the worktree is kept rather than removed
        // unasked.
        None => {
            tracing::debug!("worktree cleanup skipped: inspect unavailable");
        }
    }
}

/// One remove RPC, parsed leniently; `None` on a transport or version-skew
/// error, printed like Python's `Could not remove worktree` and never fatal.
async fn remove_request(
    client: &Arc<Client>,
    path: &str,
    force: bool,
    delete_branch: Option<bool>,
    inspect: bool,
) -> Option<WorktreeRemoveResponse> {
    remove_request_inner(client, path, force, delete_branch, inspect, true).await
}

/// The inspect probe: errors stay silent, because the probing-remove fallback
/// reports them once, with the removal wording.
async fn inspect_request(client: &Arc<Client>, path: &str) -> Option<WorktreeRemoveResponse> {
    remove_request_inner(client, path, false, None, true, false).await
}

async fn remove_request_inner(
    client: &Arc<Client>,
    path: &str,
    force: bool,
    delete_branch: Option<bool>,
    inspect: bool,
    report_errors: bool,
) -> Option<WorktreeRemoveResponse> {
    let params = WorktreeRemoveParams {
        cwd: path.to_owned(),
        force,
        delete_branch,
        inspect,
    };
    let value = match serde_json::to_value(&params) {
        Ok(value) => value,
        Err(error) => {
            if report_errors {
                print_yellow(&format!("Could not remove worktree: {error}"));
            }
            return None;
        }
    };
    match client
        .request(method::WORKSPACE_WORKTREE_REMOVE, value)
        .await
    {
        // A response that is not even the outcome object is a fault, not a
        // default: reporting it as an empty outcome would silently skip the
        // removal the user was told about.
        Ok(response) => match serde_json::from_value(response) {
            Ok(parsed) => Some(parsed),
            Err(error) => {
                if report_errors {
                    print_yellow(&format!("Could not remove worktree: {error}"));
                }
                None
            }
        },
        Err(error) => {
            if report_errors {
                print_yellow(&format!("Could not remove worktree: {error:#}"));
            }
            None
        }
    }
}

/// The dirty prompt sequence (Python `_prompt_remove_worktree` +
/// `_prompt_delete_attached_branch`), then the confirmed forced remove.
async fn dirty_flow(
    client: &Arc<Client>,
    info: &WorktreeInfo,
    probe: WorktreeRemoveResponse,
    shutdown: &mut ShutdownSignal,
) {
    print_yellow(&format!(
        "Worktree '{}' has {}.",
        info.name,
        probe.reasons.join(", ")
    ));
    print_yellow(
        "Remove it and delete its branch? This discards worktree changes, \
         untracked files, and commits.",
    );
    if !prompt_yes("Remove worktree? [y/N] ", shutdown).await {
        print_dim(&format!("Keeping worktree: {}", info.path));
        return;
    }
    // Only an attached branch (existed before this session) asks.
    let attached = probe.branch_created == Some(false);
    let attached_answer = if attached {
        print_yellow(&format!(
            "Branch '{}' existed before this session and was attached, not created by Vibe.",
            info.branch
        ));
        prompt_yes(
            &format!("Also delete branch '{}'? [y/N] ", info.branch),
            shutdown,
        )
        .await
    } else {
        false
    };
    let delete_branch = forced_delete_branch(probe.branch_created, attached_answer);
    // Python prints "Removing worktree" only after its holder recheck passes;
    // the forced remove is that recheck, so the line waits for the answer it
    // announces and a `kept_in_use` never walks it back.
    if let Some(response) = remove_request(client, &info.path, true, delete_branch, false).await {
        if let Some(line) = forced_progress_line(info, &response.outcome) {
            print_dim(&line);
        }
        report_kept_or_removed(info, &response);
    }
}

/// The `Removing worktree:` progress line for the forced remove, printed only
/// when the removal really happened: the server's holder recheck runs inside
/// the RPC, so `kept_in_use` must stay silent about a removal that never ran.
pub fn forced_progress_line(info: &WorktreeInfo, outcome: &str) -> Option<String> {
    (outcome == "removed").then(|| format!("Removing worktree: {}", info.path))
}

/// The forced call's `deleteBranch`: `None` keeps the server default (delete
/// the branch when Vibe created it), `Some` carries the attached-branch
/// prompt's answer.
pub fn forced_delete_branch(branch_created: Option<bool>, attached_answer: bool) -> Option<bool> {
    match branch_created {
        Some(true) | None => None,
        Some(false) => Some(attached_answer),
    }
}

/// The prompt outcomes the client renders after either remove call.
fn report_kept_or_removed(info: &WorktreeInfo, response: &WorktreeRemoveResponse) {
    let lines = outcome_lines(info, response);
    if lines.is_empty() {
        tracing::debug!(outcome = response.outcome, "worktree cleanup skipped");
    }
    for (color, text) in lines {
        print_line(color, &text);
    }
}

/// The stderr lines one remove outcome renders, as (color, text); empty for
/// the silent outcomes.
pub fn outcome_lines(
    info: &WorktreeInfo,
    response: &WorktreeRemoveResponse,
) -> Vec<(&'static str, String)> {
    match response.outcome.as_str() {
        "removed" => {
            let mut lines = vec![(DIM, format!("Removed worktree: {}", info.path))];
            if !response.branch_deleted {
                lines.push((DIM, format!("Kept branch: {}", info.branch)));
            }
            lines
        }
        "kept_in_use" => {
            // Python counts the other holders; an older server does not send
            // the additive field, so the count degrades to the plain wording.
            let text = match response.holders {
                Some(count) => format!(
                    "Keeping worktree {}: in use by {count} other session(s)",
                    info.path
                ),
                None => format!("Keeping worktree {}: in use by other session(s)", info.path),
            };
            vec![(DIM, text)]
        }
        "kept_dirty" | "kept_error" => vec![(
            YELLOW,
            format!("Could not remove worktree: {}", removal_reason(response)),
        )],
        _ => Vec::new(),
    }
}

/// The why behind a kept failure: the server's reasons, or the outcome itself.
pub fn removal_reason(response: &WorktreeRemoveResponse) -> String {
    if response.reasons.is_empty() {
        response.outcome.replace('_', " ")
    } else {
        response.reasons.join(", ")
    }
}

/// Python's `[y/N]` prompts: y/yes/remove (delete for the branch prompt)
/// answer yes; Ctrl+C, Ctrl+D or a signal answers no.
async fn prompt_yes(prompt: &str, shutdown: &mut ShutdownSignal) -> bool {
    let mut stderr = std::io::stderr();
    let _ = write!(stderr, "{prompt}");
    let _ = stderr.flush();
    let _ = crossterm::terminal::enable_raw_mode();
    // A detached OS thread, not the blocking pool: the runtime joins its
    // blocking tasks on drop, so a signal cancelling the read must not strand
    // the exit on a blocked terminal read. A detached thread dies with the
    // process instead.
    let (tx, rx) = tokio::sync::oneshot::channel();
    std::thread::spawn(move || {
        let _ = tx.send(read_answer());
    });
    let answer = tokio::select! {
        line = rx => line.ok().flatten(),
        _ = shutdown.wait() => None,
    };
    let _ = crossterm::terminal::disable_raw_mode();
    let _ = writeln!(stderr);
    let Some(line) = answer else {
        return false;
    };
    // Python: `input().strip().lower() in {"y", "yes", "remove"|"delete"}`.
    is_yes(
        line.trim().to_lowercase().as_str(),
        prompt.starts_with("Also delete"),
    )
}

/// Reads through crossterm, the terminal's only reader: keys the exiting TUI input thread took wait in its queue.
fn read_answer() -> Option<String> {
    let mut line = String::new();
    loop {
        let Event::Key(key) = crossterm::event::read().ok()? else {
            continue;
        };
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            _ if key.kind != KeyEventKind::Press => {}
            KeyCode::Enter => return Some(line),
            KeyCode::Char('j') if ctrl => return Some(line),
            KeyCode::Char('c' | 'd') if ctrl => return None,
            KeyCode::Backspace if line.pop().is_some() => eprint!("\x08 \x08"),
            KeyCode::Char(c) if !ctrl => {
                line.push(c);
                eprint!("{c}");
            }
            _ => {}
        }
    }
}

/// Python's accepted answers: `y`/`yes` always, plus `remove` (or `delete`
/// on the attached-branch prompt) as the long form.
pub fn is_yes(lowered: &str, branch_prompt: bool) -> bool {
    matches!(lowered, "y" | "yes") || lowered == if branch_prompt { "delete" } else { "remove" }
}

fn print_yellow(text: &str) {
    print_line(YELLOW, text);
}

pub fn print_dim(text: &str) {
    print_line(DIM, text);
}

fn print_line(color: &str, text: &str) {
    let mut stderr = std::io::stderr();
    if crate::session_exit::colors_enabled(stderr.is_terminal()) {
        let _ = writeln!(stderr, "{color}{text}{RESET}");
    } else {
        let _ = writeln!(stderr, "{text}");
    }
}
