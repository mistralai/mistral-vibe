//! The brew-managed update oracle (Rust-only; Python polls public PyPI).
//!
//! A brew install's deliverable latest is brew's own formula metadata — which
//! can lag the PyPI release the Python CLI's gateway would advertise — so the
//! check asks brew itself. The install is detected by the running executable
//! living in `brew --cellar`, brew's own answer for where its formulae are
//! installed, so a pip script that shares Homebrew's prefix is not a brew
//! install.

use std::path::{Path, PathBuf};

use tokio::process::Command;

use super::gateway::{Update, UpdateGateway, UpdateGatewayCause, UpdateGatewayError, UpdateSource};
use super::update::plain_output_env;

/// The formula this oracle watches.
pub const PROJECT_NAME: &str = "mistral-vibe";

/// How long `brew outdated` may run before the check gives up; the command
/// can hit the network for formula metadata.
const OUTDATED_TIMEOUT_SECONDS: u64 = 30;

/// How long `brew --cellar` may run before the probe gives up. The answer is
/// a local path, so this only bounds a wedged brew.
const CELLAR_TIMEOUT_SECONDS: u64 = 5;

/// True when this process is the brew-installed mistral-vibe, so brew's
/// formula metadata governs what an update can deliver. Detected through
/// brew's own answer for its Cellar; canonicalization resolves the `bin`
/// symlink a formula's executable is reached through, so a pip script under
/// Homebrew's prefix — or any other binary outside the Cellar — does not
/// match.
pub async fn is_brew_install() -> bool {
    let Some(cellar) = brew_cellar().await else {
        return false;
    };
    let Ok(executable) = std::env::current_exe() else {
        return false;
    };
    executable_in_cellar(&executable, &cellar)
}

/// brew's own answer for where its formulae are installed; empty or failed
/// answers mean brew is unavailable. `kill_on_drop` so the timeout does not
/// leave brew running.
async fn brew_cellar() -> Option<PathBuf> {
    let Ok(spawned) = tokio::time::timeout(
        std::time::Duration::from_secs(CELLAR_TIMEOUT_SECONDS),
        plain_output_env(Command::new("brew").arg("--cellar"))
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
    let cellar = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    (!cellar.is_empty()).then(|| PathBuf::from(cellar))
}

/// The oracle gateway: the latest release brew's formula metadata would
/// deliver.
pub struct BrewOracleGateway;

impl UpdateGateway for BrewOracleGateway {
    async fn fetch_update(&self) -> Result<Option<Update>, UpdateGatewayError> {
        let output = tokio::time::timeout(
            std::time::Duration::from_secs(OUTDATED_TIMEOUT_SECONDS),
            plain_output_env(Command::new("brew").args(["outdated", "--json=v2", PROJECT_NAME]))
                // Like the upgrade command: the tap auto-update costs seconds
                // of wait and its output is not an answer about this formula.
                .env("HOMEBREW_NO_AUTO_UPDATE", "1")
                .kill_on_drop(true)
                .output(),
        )
        .await;
        let output = match output {
            Ok(Ok(output)) => output,
            Ok(Err(error)) => {
                return Err(UpdateGatewayError {
                    cause: UpdateGatewayCause::RequestFailed,
                    user_message: Some(format!("brew could not be run: {error}")),
                });
            }
            Err(_) => {
                return Err(UpdateGatewayError {
                    cause: UpdateGatewayCause::RequestFailed,
                    user_message: Some("brew took too long to list outdated formulae.".to_owned()),
                });
            }
        };
        outdated_answer(
            &String::from_utf8_lossy(&output.stdout),
            &String::from_utf8_lossy(&output.stderr),
            output.status.success(),
        )
    }

    fn source(&self) -> UpdateSource {
        UpdateSource::Brew
    }
}

/// brew's answer, whatever its exit code: naming an outdated formula exits 1
/// with the JSON answer on stdout, while a genuine failure (unknown formula,
/// usage error) exits 1 with no JSON at all — the status cannot tell them
/// apart, so the JSON decides, and only a non-zero status without an answer
/// is an error.
pub fn outdated_answer(
    stdout: &str,
    stderr: &str,
    succeeded: bool,
) -> Result<Option<Update>, UpdateGatewayError> {
    if let Some(latest_version) = parse_outdated_formula(stdout) {
        return Ok(Some(Update { latest_version }));
    }
    if !succeeded {
        return Err(UpdateGatewayError {
            cause: UpdateGatewayCause::ErrorResponse,
            user_message: Some(format!(
                "brew could not list outdated formulae: {}",
                stderr.trim()
            )),
        });
    }
    Ok(None)
}

/// `brew outdated --json=v2` reports `{"formulae": [{"name": …,
/// "current_version": …}], "casks": […]}`; an up-to-date formula is absent,
/// which reads as "no update" for this project.
pub fn parse_outdated_formula(stdout: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_str(stdout).ok()?;
    let formula = value.get("formulae")?.as_array()?.iter().find(|formula| {
        formula.get("name").and_then(serde_json::Value::as_str) == Some(PROJECT_NAME)
    })?;
    let latest = formula
        .get("current_version")
        .and_then(serde_json::Value::as_str)?
        .trim()
        .trim_start_matches('v');
    let latest = revision_as_local_segment(latest);
    (!latest.is_empty()).then_some(latest)
}

/// Homebrew renders a formula revision as a trailing `_N` (`PkgVersion`), a
/// shape PEP 440 cannot parse; the revision is the version's local segment
/// (`2.25.8_1` -> `2.25.8+1`), so a revision bump orders above its bare
/// release like Homebrew's own comparison.
fn revision_as_local_segment(version: &str) -> String {
    match version.rsplit_once('_') {
        Some((base, revision))
            if !revision.is_empty() && revision.bytes().all(|b| b.is_ascii_digit()) =>
        {
            format!("{base}+{revision}")
        }
        _ => version.to_owned(),
    }
}

/// True when `executable` is the formula the Cellar holds: a symlinked
/// formula executable canonicalizes into the keg, while a pip script
/// beside Homebrew's `bin` stays outside. Component-wise containment, so a
/// sibling directory with a longer name does not match.
pub fn executable_in_cellar(executable: &Path, cellar: &Path) -> bool {
    let executable = std::fs::canonicalize(executable).unwrap_or_else(|_| executable.to_path_buf());
    let cellar = std::fs::canonicalize(cellar).unwrap_or_else(|_| cellar.to_path_buf());
    executable.starts_with(cellar)
}
