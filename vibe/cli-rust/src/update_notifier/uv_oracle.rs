//! The uv-managed update oracle (Rust-only; Python polls public PyPI).
//!
//! Python's gateway polls pypi.org regardless of the user's package source,
//! so it advertises versions the local uv configuration — private index,
//! pinned `exclude-newer`, per-package rules — may never install, and the
//! update then ends in a confusing no-op. When mistral-vibe is installed as
//! a uv tool, uv is the only component that can compute the eligible latest
//! release, so the check asks uv itself via `uv tool list --outdated`.

use std::path::Path;

use tokio::process::Command;

use super::gateway::{Update, UpdateGateway, UpdateGatewayCause, UpdateGatewayError};
use super::update::plain_output_env;

/// The tool this oracle watches.
pub const PROJECT_NAME: &str = "mistral-vibe";

/// How long `uv tool list --outdated` may run before the check gives up;
/// the command resolves every installed tool, so it can be slow.
const ORACLE_TIMEOUT_SECONDS: u64 = 30;

/// How long `uv tool dir` may run before the probe gives up. The answer is a
/// local path, so this only bounds a wedged uv — which would otherwise hang
/// the check before the bounded outdated list even starts.
const TOOL_DIR_TIMEOUT_SECONDS: u64 = 5;

/// True when this process is the uv-managed mistral-vibe install, so uv's
/// configuration governs what an update can deliver. A receipt only proves
/// some uv install exists; the running executable must be that install, or a
/// leftover uv tool next to a brew, pip, or cargo binary would send that
/// binary's checks to uv. Detected through uv's own answer for the tool
/// directory plus the install receipt, the record of exactly the
/// configuration the tool was installed under. The `uv tool dir` subprocess
/// runs through tokio so a current-thread runtime is never blocked on it.
pub async fn is_uv_managed() -> bool {
    this_install_receipt().await.is_some()
}

/// The receipt of the uv install this process is; `None` when uv is
/// unavailable, has no mistral-vibe tool, or the running executable is some
/// other binary.
pub async fn this_install_receipt() -> Option<(std::path::PathBuf, String)> {
    let tool_dir = uv_tool_dir().await?;
    let receipt = std::fs::read_to_string(receipt_path(&tool_dir)).ok()?;
    let executable = std::env::current_exe().ok()?;
    if !executable_matches_uv_install(&executable, &tool_dir, &receipt) {
        return None;
    }
    Some((tool_dir, receipt))
}

/// uv's own answer for the tool directory; empty or failed answers mean uv
/// is unavailable. `kill_on_drop` so the timeout does not leave uv running.
async fn uv_tool_dir() -> Option<std::path::PathBuf> {
    let Ok(spawned) = tokio::time::timeout(
        std::time::Duration::from_secs(TOOL_DIR_TIMEOUT_SECONDS),
        plain_output_env(Command::new("uv").args(["tool", "dir"]))
            .kill_on_drop(true)
            .output(),
    )
    .await
    else {
        return None;
    };
    let output = spawned.ok()?;
    if !output.status.success() {
        return None;
    }
    let tool_dir = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    (!tool_dir.is_empty()).then(|| std::path::PathBuf::from(tool_dir))
}

/// The install receipt uv writes for every tool it manages.
pub fn receipt_exists(tool_dir: &Path) -> bool {
    receipt_path(tool_dir).is_file()
}

fn receipt_path(tool_dir: &Path) -> std::path::PathBuf {
    tool_dir.join(PROJECT_NAME).join("uv-receipt.toml")
}

/// True when `executable` is the install `receipt` records under `tool_dir`:
/// it lives inside that tool's directory, or it is an entrypoint
/// `install-path` (uv's shim directory). A brew, pip, or cargo binary next
/// to a leftover uv tool does not match.
pub fn executable_matches_uv_install(executable: &Path, tool_dir: &Path, receipt: &str) -> bool {
    if path_within(executable, &tool_dir.join(PROJECT_NAME)) {
        return true;
    }
    install_paths_from_receipt(receipt)
        .iter()
        .any(|install_path| paths_same(executable, install_path))
}

/// The `install-path` of every entrypoint the receipt records, in either
/// layout uv has written: an inline-table array or an array of tables.
fn install_paths_from_receipt(receipt: &str) -> Vec<std::path::PathBuf> {
    let Ok(document) = receipt.parse::<toml_edit::DocumentMut>() else {
        return Vec::new();
    };
    let Some(entrypoints) = document
        .get("tool")
        .and_then(|tool| tool.get("entrypoints"))
    else {
        return Vec::new();
    };
    if let Some(values) = entrypoints.as_array() {
        return values
            .iter()
            .filter_map(|value| {
                value
                    .as_inline_table()
                    .and_then(|table| table.get("install-path"))
                    .and_then(toml_edit::Value::as_str)
            })
            .map(std::path::PathBuf::from)
            .collect();
    }
    let Some(tables) = entrypoints.as_array_of_tables() else {
        return Vec::new();
    };
    tables
        .iter()
        .filter_map(|table| table.get("install-path").and_then(toml_edit::Item::as_str))
        .map(str::to_owned)
        .map(std::path::PathBuf::from)
        .collect()
}

/// Same file, following the symlinks a shim directory may carry.
fn paths_same(left: &Path, right: &Path) -> bool {
    if left == right {
        return true;
    }
    match (std::fs::canonicalize(left), std::fs::canonicalize(right)) {
        (Ok(left), Ok(right)) => left == right,
        _ => false,
    }
}

/// Component-wise containment, so a sibling directory with a longer name
/// does not match.
fn path_within(path: &Path, dir: &Path) -> bool {
    let path = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let dir = std::fs::canonicalize(dir).unwrap_or_else(|_| dir.to_path_buf());
    path.starts_with(dir)
}

/// The git source a git-pinned tool install records in its receipt, for the
/// update hint. Registry installs return `None`. uv never lists a git tool
/// as outdated — so no pending update can be detected — but `uv tool
/// upgrade` re-resolves the branch and does upgrade it (verified against a
/// real repo: `Updated gitoracle v0.1.0 -> v0.2.0` after the branch moved).
/// Only the install this process is answers.
pub async fn git_source() -> Option<String> {
    let (_tool_dir, receipt) = this_install_receipt().await?;
    git_source_from_receipt(&receipt)
}

/// The `tool.requirements` entry for this project, read for a `git` key.
pub fn git_source_from_receipt(receipt: &str) -> Option<String> {
    let document: toml_edit::DocumentMut = receipt.parse().ok()?;
    let requirements = document
        .get("tool")
        .and_then(|tool| tool.get("requirements"))
        .and_then(|requirements| requirements.as_array())?;
    for requirement in requirements {
        let Some(table) = requirement.as_inline_table() else {
            continue;
        };
        if table.get("name").and_then(|name| name.as_str()) != Some(PROJECT_NAME) {
            continue;
        }
        let git = table.get("git").and_then(|git| git.as_str())?;
        return Some(format!("git+{git}"));
    }
    None
}

/// The oracle gateway: the latest release uv's own resolution would deliver.
pub struct UvOracleGateway;

impl UpdateGateway for UvOracleGateway {
    async fn fetch_update(&self) -> Result<Option<Update>, UpdateGatewayError> {
        let output = tokio::time::timeout(
            std::time::Duration::from_secs(ORACLE_TIMEOUT_SECONDS),
            plain_output_env(Command::new("uv").args(["tool", "list", "--outdated"]))
                .kill_on_drop(true)
                .output(),
        )
        .await;
        let output = match output {
            Ok(Ok(output)) if output.status.success() => output,
            Ok(Ok(output)) => {
                return Err(UpdateGatewayError {
                    cause: UpdateGatewayCause::ErrorResponse,
                    user_message: Some(format!(
                        "uv could not list outdated tools: {}",
                        String::from_utf8_lossy(&output.stderr).trim()
                    )),
                });
            }
            Ok(Err(error)) => {
                return Err(UpdateGatewayError {
                    cause: UpdateGatewayCause::RequestFailed,
                    user_message: Some(format!("uv could not be run: {error}")),
                });
            }
            Err(_) => {
                return Err(UpdateGatewayError {
                    cause: UpdateGatewayCause::RequestFailed,
                    user_message: Some("uv took too long to list outdated tools.".to_owned()),
                });
            }
        };
        let stdout = String::from_utf8_lossy(&output.stdout);
        Ok(parse_outdated_tool_list(&stdout).map(|latest_version| Update { latest_version }))
    }
}

/// `uv tool list --outdated` prints one entry per outdated tool as
/// `mistral-vibe v2.25.4 [latest: 2.25.5]`; tools that are up to date or not
/// uv-managed do not appear, which reads as "no update" for this project.
pub fn parse_outdated_tool_list(stdout: &str) -> Option<String> {
    for line in stdout.lines() {
        let line = line.trim();
        // The tool line is `mistral-vibe v2.25.4 [latest: 2.25.5]`; bound the
        // prefix match at whitespace so no sibling tool's name collides.
        let Some(rest) = line
            .strip_prefix(PROJECT_NAME)
            .filter(|rest| rest.starts_with(' '))
        else {
            continue;
        };
        let Some(latest) = rest.split("[latest:").nth(1) else {
            continue;
        };
        let latest = latest.trim_end_matches(']').trim().trim_start_matches('v');
        if latest.is_empty() {
            continue;
        }
        return Some(latest.to_owned());
    }
    None
}
